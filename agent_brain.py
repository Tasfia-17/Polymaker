"""
AgentBrain: LLM reasoning layer for PolyMaker.

Before each requote cycle the brain:
  1. Fetches recent news headlines relevant to the market question
  2. Calls an LLM (OpenAI-compatible) to reason about:
       - Resolution risk: is this market about to resolve?
       - Sentiment shift: does news change the fair-value estimate?
       - Volatility regime: should risk_aversion be widened?
  3. Returns a MarketDecision that the engine applies

The brain is stateless per call and caches decisions for
CACHE_TTL seconds to avoid hammering the LLM on every tick.

Falls back gracefully (returns neutral decision) if:
  - No API key is set
  - LLM call fails
  - News fetch fails

Set OPENAI_API_KEY (or any OpenAI-compatible key) in .env.
Override the base URL with LLM_BASE_URL for local models.
"""
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

CACHE_TTL = 120  # seconds between LLM calls per market
NEWS_API = "https://newsapi.org/v2/everything"


@dataclass
class MarketDecision:
    """What the brain decided for a single market."""
    token_id: str
    skip: bool = False               # skip quoting entirely
    risk_aversion_multiplier: float = 1.0   # multiply engine's base risk_aversion
    reasoning: str = ""              # LLM chain-of-thought (logged + exposed in /status)
    resolution_risk: str = "low"     # "low" | "medium" | "high"
    timestamp: float = field(default_factory=time.monotonic)


class AgentBrain:
    """
    LLM agent that reasons about each market before the engine quotes.

    The agent uses a structured prompt that forces the LLM to output JSON,
    making its decisions machine-readable and auditable.
    """

    def __init__(self):
        self._api_key = os.getenv("OPENAI_API_KEY", "")
        self._base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self._model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self._news_key = os.getenv("NEWS_API_KEY", "")
        self._cache: dict[str, MarketDecision] = {}
        self._enabled = bool(self._api_key)
        if not self._enabled:
            logger.info("AgentBrain: no OPENAI_API_KEY set, running in passthrough mode")

    def decide(self, question: str, token_id: str, mid: float, category: str) -> MarketDecision:
        """
        Return a MarketDecision for this market.
        Uses cached result if fresh enough.
        """
        cached = self._cache.get(token_id)
        if cached and (time.monotonic() - cached.timestamp) < CACHE_TTL:
            return cached

        if not self._enabled:
            return MarketDecision(token_id=token_id)

        try:
            decision = self._run(question, token_id, mid, category)
        except Exception as e:
            logger.warning(f"AgentBrain failed for {question[:40]}: {e}")
            decision = MarketDecision(token_id=token_id)

        self._cache[token_id] = decision
        return decision

    def _run(self, question: str, token_id: str, mid: float, category: str) -> MarketDecision:
        headlines = self._fetch_headlines(question)

        prompt = f"""You are a prediction market risk manager for an automated market maker.

Market question: "{question}"
Category: {category}
Current mid-price: {mid:.3f} (probability {mid*100:.1f}%)

Recent news headlines:
{headlines or "(no headlines found)"}

Analyze this market and respond with ONLY valid JSON in this exact format:
{{
  "skip": false,
  "risk_aversion_multiplier": 1.0,
  "resolution_risk": "low",
  "reasoning": "one sentence explanation"
}}

Rules:
- Set "skip": true if the market is likely to resolve within 24 hours based on the question or news
- Set "risk_aversion_multiplier" between 0.5 (calm, quote tighter) and 3.0 (volatile, quote wider)
- Set "resolution_risk" to "low", "medium", or "high"
- "reasoning" must be one sentence, no more

Respond with JSON only. No markdown, no explanation outside the JSON."""

        resp = requests.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 200,
            },
            timeout=10,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        parsed = json.loads(content)

        decision = MarketDecision(
            token_id=token_id,
            skip=bool(parsed.get("skip", False)),
            risk_aversion_multiplier=float(parsed.get("risk_aversion_multiplier", 1.0)),
            reasoning=str(parsed.get("reasoning", "")),
            resolution_risk=str(parsed.get("resolution_risk", "low")),
        )

        logger.info(
            f"AgentBrain [{question[:35]}] "
            f"skip={decision.skip} "
            f"ra_mult={decision.risk_aversion_multiplier:.2f} "
            f"risk={decision.resolution_risk} | {decision.reasoning}"
        )
        return decision

    def _fetch_headlines(self, question: str, n: int = 5) -> str:
        """Fetch top news headlines relevant to the market question."""
        if not self._news_key:
            return ""
        try:
            # Extract key terms (first 6 words of question)
            query = " ".join(question.split()[:6]).strip("?")
            r = requests.get(
                NEWS_API,
                params={
                    "q": query,
                    "pageSize": n,
                    "sortBy": "publishedAt",
                    "apiKey": self._news_key,
                },
                timeout=5,
            )
            if r.status_code != 200:
                return ""
            articles = r.json().get("articles", [])
            return "\n".join(
                f"- {a['title']} ({a['source']['name']})"
                for a in articles[:n]
                if a.get("title")
            )
        except Exception:
            return ""

    def all_decisions(self) -> list[dict]:
        """Return all cached decisions for /status endpoint."""
        return [
            {
                "token_id": d.token_id,
                "skip": d.skip,
                "risk_aversion_multiplier": d.risk_aversion_multiplier,
                "resolution_risk": d.resolution_risk,
                "reasoning": d.reasoning,
            }
            for d in self._cache.values()
        ]
