"""
Market selector: ranks Polymarket markets by expected MM profitability.

Scoring formula (higher = better for market making):
  score = spread_score * liquidity_score * fee_score * (1 - resolution_proximity)

Filters:
  - enableOrderBook = True (CLOB markets only)
  - active = True, closed = False
  - mid-price in [0.10, 0.90] (avoid extreme longshots — SF1 from arXiv:2604.24366)
  - volume24hr > min_volume
  - feesEnabled = True (geopolitics markets have 0% fees — skip)
"""
from __future__ import annotations
import requests
from dataclasses import dataclass
from fees import Category, get_fee_params

GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class MarketInfo:
    condition_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    volume_24h: float
    liquidity: float
    category: Category
    tick_size: float
    end_date_iso: str | None
    score: float


def _parse_category(tags: list[str]) -> Category:
    mapping = {
        "crypto": Category.CRYPTO,
        "sports": Category.SPORTS,
        "finance": Category.FINANCE,
        "politics": Category.POLITICS,
        "economics": Category.ECONOMICS,
        "culture": Category.CULTURE,
        "weather": Category.WEATHER,
        "tech": Category.TECH,
        "mentions": Category.MENTIONS,
        "geopolitics": Category.GEOPOLITICS,
    }
    for tag in (t.lower() for t in tags):
        if tag in mapping:
            return mapping[tag]
    return Category.OTHER


def fetch_candidate_markets(
    limit: int = 50,
    min_volume_24h: float = 1000.0,
    min_liquidity: float = 500.0,
) -> list[MarketInfo]:
    """
    Fetch and rank markets suitable for market making.
    Returns list sorted by score descending.
    """
    import json

    resp = requests.get(
        f"{GAMMA_API}/markets",
        params={
            "active": "true",
            "closed": "false",
            "enableOrderBook": "true",
            "limit": limit,
        },
        timeout=10,
    )
    resp.raise_for_status()
    raw = resp.json()
    markets = raw.get("data", raw) if isinstance(raw, dict) else raw

    results: list[MarketInfo] = []

    for m in markets:
        try:
            # Parse token IDs
            clob_ids = m.get("clobTokenIds", "[]")
            if isinstance(clob_ids, str):
                clob_ids = json.loads(clob_ids)
            if len(clob_ids) < 2:
                continue

            # Parse prices
            prices = m.get("outcomePrices", "[0.5, 0.5]")
            if isinstance(prices, str):
                prices = json.loads(prices)
            yes_price = float(prices[0]) if prices else 0.5

            best_bid = float(m.get("bestBid") or yes_price - 0.01)
            best_ask = float(m.get("bestAsk") or yes_price + 0.01)
            mid = (best_bid + best_ask) / 2.0
            spread = best_ask - best_bid

            # Filter: avoid extreme probabilities (SF1: spreads 1300-1800bps at extremes)
            if not (0.10 <= mid <= 0.90):
                continue

            volume_24h = float(m.get("volume24hr") or 0)
            liquidity = float(m.get("liquidity") or 0)

            if volume_24h < min_volume_24h or liquidity < min_liquidity:
                continue

            tags = [t.get("label", "") for t in m.get("tags", [])]
            category = _parse_category(tags)

            # Skip zero-fee geopolitics (no rebate)
            if category == Category.GEOPOLITICS:
                continue

            fee_params = get_fee_params(category)
            tick_size = float(m.get("minimumTickSize") or 0.01)

            # Scoring: reward tight spreads (more room to earn), high volume, good fees
            spread_score = min(spread / 0.10, 1.0)          # wider spread = more room
            volume_score = min(volume_24h / 10000.0, 1.0)
            fee_score = fee_params.taker_rate                 # higher taker fee = more rebate pool
            score = spread_score * volume_score * fee_score

            results.append(MarketInfo(
                condition_id=m.get("conditionId", m.get("id", "")),
                question=m.get("question", "")[:80],
                yes_token_id=clob_ids[0],
                no_token_id=clob_ids[1],
                best_bid=best_bid,
                best_ask=best_ask,
                mid=mid,
                spread=spread,
                volume_24h=volume_24h,
                liquidity=liquidity,
                category=category,
                tick_size=tick_size,
                end_date_iso=m.get("endDate"),
                score=score,
            ))
        except (KeyError, ValueError, IndexError):
            continue

    results.sort(key=lambda x: x.score, reverse=True)
    return results
