"""
Unit tests for fee model, quoting, and inventory.
Run: python -m pytest tests/ -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from fees import Category, get_fee_params, taker_fee, effective_spread_needed
from quoting import compute_quotes, estimate_volatility, logit, sigmoid
from inventory import Position, InventoryManager


# ── Fee model ──────────────────────────────────────────────────────────────

class TestFees:
    def test_crypto_taker_fee(self):
        params = get_fee_params(Category.CRYPTO)
        # fee = C * 0.07 * p * (1-p)^1 at p=0.5: 100 * 0.07 * 0.5 * 0.5 = 1.75
        fee = taker_fee(100, 0.5, params)
        assert abs(fee - 1.75) < 1e-6

    def test_other_exponent_2(self):
        params = get_fee_params(Category.OTHER)
        # fee = C * 0.05 * p * (1-p)^2 at p=0.5: 100 * 0.05 * 0.5 * 0.25 = 0.625
        fee = taker_fee(100, 0.5, params)
        assert abs(fee - 0.625) < 1e-6

    def test_weather_exponent_half(self):
        params = get_fee_params(Category.WEATHER)
        # fee = C * 0.05 * p * (1-p)^0.5 at p=0.5: 100 * 0.05 * 0.5 * 0.5^0.5
        import math
        expected = 100 * 0.05 * 0.5 * (0.5 ** 0.5)
        fee = taker_fee(100, 0.5, params)
        assert abs(fee - expected) < 1e-6

    def test_geopolitics_zero_fee(self):
        params = get_fee_params(Category.GEOPOLITICS)
        assert taker_fee(1000, 0.5, params) == 0.0

    def test_maker_never_pays(self):
        # Maker fee rate is always 0
        for cat in Category:
            params = get_fee_params(cat)
            assert params.taker_rate >= 0  # taker pays
            # maker_rebate_pct >= 0 (maker earns or breaks even)
            assert params.maker_rebate_pct >= 0

    def test_effective_spread_positive(self):
        params = get_fee_params(Category.SPORTS)
        spread = effective_spread_needed(0.5, params)
        assert spread > 0

    def test_effective_spread_respects_minimum(self):
        params = get_fee_params(Category.SPORTS)
        spread = effective_spread_needed(0.5, params, min_spread=0.05)
        assert spread >= 0.05


# ── Quoting ────────────────────────────────────────────────────────────────

class TestQuoting:
    def test_logit_sigmoid_inverse(self):
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            assert abs(sigmoid(logit(p)) - p) < 1e-9

    def test_bid_less_than_ask(self):
        q = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.01, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0,
            min_spread=0.01,
        )
        assert q.bid < q.ask

    def test_quotes_in_valid_range(self):
        q = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.01, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0,
            min_spread=0.01,
        )
        assert 0 < q.bid < 1
        assert 0 < q.ask < 1

    def test_inventory_skew_long(self):
        # Long inventory → bid skews down (less eager to buy more)
        q_flat = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.01, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0, min_spread=0.01,
        )
        q_long = compute_quotes(
            mid=0.5, inventory_yes=50, max_inventory=100,
            sigma_sq=0.01, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0, min_spread=0.01,
        )
        assert q_long.bid <= q_flat.bid

    def test_higher_volatility_widens_spread(self):
        q_low = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.001, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0, min_spread=0.0,
        )
        q_high = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.1, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0, min_spread=0.0,
        )
        assert q_high.spread >= q_low.spread

    def test_min_spread_enforced(self):
        q = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.0001, time_to_expiry=0.01,
            risk_aversion=0.01, order_arrival=100.0,
            min_spread=0.05,
        )
        assert q.spread >= 0.05 - 1e-6  # allow tick rounding

    def test_tick_size_respected(self):
        q = compute_quotes(
            mid=0.5, inventory_yes=0, max_inventory=100,
            sigma_sq=0.01, time_to_expiry=0.5,
            risk_aversion=0.1, order_arrival=10.0,
            min_spread=0.01, tick_size=0.01,
        )
        # Prices should be multiples of tick_size
        assert abs(round(q.bid / 0.01) * 0.01 - q.bid) < 1e-5
        assert abs(round(q.ask / 0.01) * 0.01 - q.ask) < 1e-5

    def test_volatility_estimate_default_on_short_history(self):
        v = estimate_volatility([])
        assert v == 0.01  # default prior

    def test_volatility_estimate_positive(self):
        prices = [0.4, 0.45, 0.42, 0.48, 0.5, 0.47, 0.52]
        v = estimate_volatility(prices)
        assert v > 0


# ── Inventory ──────────────────────────────────────────────────────────────

class TestInventory:
    def test_buy_fill_increases_inventory(self):
        pos = Position(token_id="t1")
        pos.record_fill("SELL", price=0.5, shares=10, usdc=5.0)
        assert pos.yes_shares == 10
        assert abs(pos.avg_yes_price - 0.5) < 1e-9

    def test_sell_fill_decreases_inventory(self):
        pos = Position(token_id="t1")
        pos.record_fill("SELL", price=0.5, shares=10, usdc=5.0)
        pos.record_fill("BUY", price=0.6, shares=5, usdc=3.0)
        assert pos.yes_shares == 5

    def test_realized_pnl_on_sell(self):
        pos = Position(token_id="t1")
        pos.record_fill("SELL", price=0.5, shares=10, usdc=5.0)  # bought at 0.5
        pos.record_fill("BUY", price=0.6, shares=10, usdc=6.0)   # sold at 0.6
        assert abs(pos.realized_pnl - 1.0) < 1e-6  # (0.6-0.5)*10

    def test_unrealized_pnl(self):
        pos = Position(token_id="t1")
        pos.record_fill("SELL", price=0.5, shares=10, usdc=5.0)
        assert abs(pos.unrealized_pnl(0.6) - 1.0) < 1e-6

    def test_stop_loss_trigger(self):
        pos = Position(token_id="t1")
        pos.record_fill("SELL", price=0.5, shares=10, usdc=5.0)
        assert pos.should_stop(current_mid=0.3, stop_loss_usdc=-1.0)
        assert not pos.should_stop(current_mid=0.6, stop_loss_usdc=-1.0)

    def test_max_shares_respects_limit(self):
        mgr = InventoryManager(max_position_usdc=50.0, stop_loss_usdc=-10.0)
        max_s = mgr.max_shares_to_buy("t1", price=0.5)
        assert abs(max_s - 100.0) < 1e-6  # 50 / 0.5

    def test_max_shares_zero_when_full(self):
        mgr = InventoryManager(max_position_usdc=50.0, stop_loss_usdc=-10.0)
        pos = mgr.get("t1")
        pos.yes_shares = 100
        pos.avg_yes_price = 0.5  # 100 * 0.5 = 50 USDC deployed
        max_s = mgr.max_shares_to_buy("t1", price=0.5)
        assert max_s <= 0
