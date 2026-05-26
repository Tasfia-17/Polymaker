"""
Fee model for Polymarket (post March 30, 2026 rollout).

Exact formula from docs.polymarket.com/trading/fees:
  fee = C × feeRate × p × (1 - p)^exponent

where exponent varies by category:
  1   → Crypto, Sports, Finance, Politics, Culture, Tech, Mentions, Geopolitics
  2   → Other/General
  0.5 → Economics, Weather

Makers NEVER pay fees. Maker rebate is paid daily in USDC.
Rebate weighting: fee_equivalent = C × feeRate × p × (1 - p)
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class Category(str, Enum):
    CRYPTO = "crypto"
    SPORTS = "sports"
    FINANCE = "finance"
    POLITICS = "politics"
    ECONOMICS = "economics"
    CULTURE = "culture"
    WEATHER = "weather"
    TECH = "tech"
    MENTIONS = "mentions"
    GEOPOLITICS = "geopolitics"
    OTHER = "other"


# (taker_fee_rate, maker_rebate_pct, exponent)
_FEE_TABLE: dict[Category, tuple[float, float, float]] = {
    Category.CRYPTO:      (0.07, 0.20, 1.0),
    Category.SPORTS:      (0.03, 0.25, 1.0),
    Category.FINANCE:     (0.04, 0.25, 1.0),
    Category.POLITICS:    (0.04, 0.25, 1.0),
    Category.ECONOMICS:   (0.05, 0.25, 0.5),
    Category.CULTURE:     (0.05, 0.25, 1.0),
    Category.WEATHER:     (0.05, 0.25, 0.5),
    Category.TECH:        (0.04, 0.25, 1.0),
    Category.MENTIONS:    (0.04, 0.25, 1.0),
    Category.GEOPOLITICS: (0.00, 0.00, 1.0),
    Category.OTHER:       (0.05, 0.25, 2.0),
}


@dataclass(frozen=True)
class FeeParams:
    taker_rate: float
    maker_rebate_pct: float
    exponent: float


def get_fee_params(category: Category) -> FeeParams:
    r, rebate, exp = _FEE_TABLE[category]
    return FeeParams(taker_rate=r, maker_rebate_pct=rebate, exponent=exp)


def taker_fee(shares: float, price: float, params: FeeParams) -> float:
    """USDC fee paid by taker on a fill."""
    return shares * params.taker_rate * price * ((1 - price) ** params.exponent)


def maker_fee_equivalent(shares: float, price: float, params: FeeParams) -> float:
    """
    Fee-equivalent used for rebate weighting (not an actual charge).
    rebate_share = your_fee_equiv / total_fee_equiv_in_pool
    """
    return shares * params.taker_rate * price * (1 - price)


def effective_spread_needed(price: float, params: FeeParams, min_spread: float = 0.0) -> float:
    """
    Minimum half-spread a maker needs to quote to break even after adverse selection.
    Accounts for taker fee that the aggressor pays (which reduces effective cost to maker).
    """
    fee_at_price = params.taker_rate * price * ((1 - price) ** params.exponent)
    return max(min_spread, fee_at_price * 0.5)
