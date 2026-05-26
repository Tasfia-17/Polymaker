"""
Avellaneda-Stoikov market making model adapted for prediction markets.

Standard A-S model (Avellaneda & Stoikov, 2008):
  reservation price: r = s - q * γ * σ² * (T - t)
  optimal spread:    δ = γ * σ² * (T - t) + (2/γ) * ln(1 + γ/κ)

For prediction markets, price s ∈ (0, 1) and σ² is estimated from
recent mid-price history. We work in logit space to respect the [0,1] bound.

Inventory skew: when holding q > 0 YES shares, skew bid down / ask up
to reduce inventory risk.

Reference: arXiv:2510.15205 "Toward Black-Scholes for Prediction Markets"
"""
from __future__ import annotations
import math
import numpy as np
from dataclasses import dataclass


@dataclass
class QuotePair:
    bid: float   # price to buy YES (maker bid)
    ask: float   # price to sell YES (maker ask)
    mid: float   # reservation price
    spread: float


def logit(p: float) -> float:
    p = max(1e-6, min(1 - 1e-6, p))
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def estimate_volatility(mid_prices: list[float], window: int = 20) -> float:
    """
    Estimate σ² in logit space from recent mid-price history.
    Returns variance of logit returns.
    """
    if len(mid_prices) < 2:
        return 0.01  # default prior
    logits = [logit(p) for p in mid_prices[-window:]]
    diffs = np.diff(logits)
    return float(np.var(diffs)) if len(diffs) > 0 else 0.01


def compute_quotes(
    mid: float,
    inventory_yes: float,    # signed: positive = long YES, negative = short YES
    max_inventory: float,    # maximum allowed inventory in shares
    sigma_sq: float,         # logit-space variance estimate
    time_to_expiry: float,   # fraction of market lifetime remaining (0-1)
    risk_aversion: float,    # γ: higher = wider quotes
    order_arrival: float,    # κ: order arrival rate estimate
    min_spread: float,       # minimum half-spread from fee model
    tick_size: float = 0.01,
) -> QuotePair:
    """
    Compute optimal bid/ask quotes using Avellaneda-Stoikov in logit space.

    Returns prices clamped to [tick_size, 1 - tick_size] and rounded to tick.
    """
    x = logit(mid)

    # Inventory skew in logit space
    q = inventory_yes / max(max_inventory, 1.0)  # normalized [-1, 1]
    reservation_logit = x - q * risk_aversion * sigma_sq * time_to_expiry

    # Optimal half-spread in logit space
    if order_arrival > 0:
        half_spread_logit = (
            risk_aversion * sigma_sq * time_to_expiry / 2.0
            + math.log(1.0 + risk_aversion / order_arrival) / risk_aversion
        )
    else:
        half_spread_logit = risk_aversion * sigma_sq * time_to_expiry

    # Convert back to probability space
    reservation_price = sigmoid(reservation_logit)
    bid_raw = sigmoid(reservation_logit - half_spread_logit)
    ask_raw = sigmoid(reservation_logit + half_spread_logit)

    # Enforce minimum spread from fee model
    actual_half_spread = max((ask_raw - bid_raw) / 2.0, min_spread)
    bid_raw = reservation_price - actual_half_spread
    ask_raw = reservation_price + actual_half_spread

    # Clamp and round to tick
    def snap(p: float) -> float:
        p = max(tick_size, min(1.0 - tick_size, p))
        return round(round(p / tick_size) * tick_size, 6)

    bid = snap(bid_raw)
    ask = snap(ask_raw)

    # Ensure bid < ask after snapping
    if bid >= ask:
        ask = snap(bid + tick_size)

    return QuotePair(bid=bid, ask=ask, mid=reservation_price, spread=ask - bid)
