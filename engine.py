"""
Core market making engine.

Per-market loop:
  1. Receive book update from WebSocket
  2. Compute Avellaneda-Stoikov quotes (fee-aware, inventory-skewed)
  3. Cancel stale orders if price moved > threshold
  4. Place new bid + ask with builder code
  5. Check stop-loss; exit market if triggered

The engine is event-driven: book updates trigger requotes.
A background task handles periodic position reconciliation.
"""
from __future__ import annotations
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from book_feed import OrderBook
from clob_client import PolymakerClient
from fees import Category, get_fee_params, effective_spread_needed
from inventory import InventoryManager, Position
from market_selector import MarketInfo
from quoting import compute_quotes, estimate_volatility

logger = logging.getLogger(__name__)

# Requote if best bid/ask moved more than this fraction
REQUOTE_THRESHOLD = 0.005
# Minimum time between requotes per market (seconds) — avoid spam
MIN_REQUOTE_INTERVAL = 2.0
# Order size in USDC per side
ORDER_SIZE_USDC = float(os.getenv("ORDER_SIZE_USDC", "5.0"))


@dataclass
class MarketState:
    info: MarketInfo
    last_requote: float = 0.0
    last_bid_price: float = 0.0
    last_ask_price: float = 0.0
    active: bool = True


class MakerEngine:
    """
    Runs market making across multiple markets concurrently.
    Each market gets an independent quote loop driven by book events.
    """

    def __init__(
        self,
        client: PolymakerClient,
        inventory: InventoryManager,
        risk_aversion: float = 0.1,
        order_arrival: float = 10.0,
    ):
        self.client = client
        self.inventory = inventory
        self.risk_aversion = risk_aversion
        self.order_arrival = order_arrival
        self._markets: dict[str, MarketState] = {}
        self._requote_queue: asyncio.Queue = asyncio.Queue()

    def add_market(self, info: MarketInfo) -> None:
        self._markets[info.yes_token_id] = MarketState(info=info)

    def on_book_update(self, token_id: str, book: OrderBook) -> None:
        """Called by BookFeed on every update. Enqueues requote if needed."""
        state = self._markets.get(token_id)
        if not state or not state.active:
            return

        if book.resolved:
            logger.info(f"Market resolved, stopping: {state.info.question[:40]}")
            state.active = False
            self._cancel_open_orders(state.info)
            return

        mid = book.mid
        if mid is None:
            return

        now = time.monotonic()
        if now - state.last_requote < MIN_REQUOTE_INTERVAL:
            return

        # Only requote if price moved meaningfully
        bid_moved = abs((book.best_bid or mid) - state.last_bid_price) > REQUOTE_THRESHOLD
        ask_moved = abs((book.best_ask or mid) - state.last_ask_price) > REQUOTE_THRESHOLD
        no_orders = (
            self.inventory.get(token_id).open_bid_id is None
            and self.inventory.get(token_id).open_ask_id is None
        )

        if bid_moved or ask_moved or no_orders:
            self._requote_queue.put_nowait((token_id, book))

    async def run(self) -> None:
        """Process requote queue. Run as asyncio task."""
        while True:
            token_id, book = await self._requote_queue.get()
            try:
                await self._requote(token_id, book)
            except Exception as e:
                logger.error(f"Requote error {token_id}: {e}")
            finally:
                self._requote_queue.task_done()

    async def _requote(self, token_id: str, book: OrderBook) -> None:
        state = self._markets.get(token_id)
        if not state or not state.active:
            return

        mid = book.mid
        if mid is None:
            return

        pos = self.inventory.get(token_id)
        info = state.info

        # Stop-loss check
        if pos.should_stop(mid, self.inventory.stop_loss_usdc):
            logger.warning(
                f"Stop-loss triggered on {info.question[:40]}: "
                f"PnL={pos.unrealized_pnl(mid):.2f}"
            )
            state.active = False
            self._cancel_open_orders(info)
            return

        # Compute fee-aware minimum spread
        fee_params = get_fee_params(info.category)
        min_half_spread = effective_spread_needed(mid, fee_params)

        # Estimate volatility from mid-price history
        sigma_sq = estimate_volatility(list(book.mid_history))

        # Time to expiry (rough estimate: 0.5 if unknown)
        time_to_expiry = 0.5

        # Compute A-S quotes
        max_shares = self.inventory.max_shares_to_buy(token_id, mid)
        quotes = compute_quotes(
            mid=mid,
            inventory_yes=pos.net_inventory(),
            max_inventory=max(max_shares, 1.0),
            sigma_sq=sigma_sq,
            time_to_expiry=time_to_expiry,
            risk_aversion=self.risk_aversion,
            order_arrival=self.order_arrival,
            min_spread=min_half_spread,
            tick_size=book.tick_size,
        )

        # Cancel existing orders
        self._cancel_open_orders(info)

        # Size: USDC / price → shares
        bid_size = round(ORDER_SIZE_USDC / quotes.bid, 2) if quotes.bid > 0 else 0
        ask_size = round(ORDER_SIZE_USDC / quotes.ask, 2) if quotes.ask > 0 else 0

        # Place bid (buy YES)
        if bid_size >= 1.0 and max_shares > 0:
            resp = self.client.place_limit_order(
                token_id=token_id,
                side="BUY",
                price=quotes.bid,
                size=bid_size,
            )
            pos.open_bid_id = resp.get("order_id")

        # Place ask (sell YES)
        if ask_size >= 1.0 and pos.yes_shares > 0:
            resp = self.client.place_limit_order(
                token_id=token_id,
                side="SELL",
                price=quotes.ask,
                size=min(ask_size, pos.yes_shares),
            )
            pos.open_ask_id = resp.get("order_id")

        state.last_bid_price = quotes.bid
        state.last_ask_price = quotes.ask
        state.last_requote = time.monotonic()

        logger.debug(
            f"{info.question[:35]} | "
            f"mid={mid:.3f} bid={quotes.bid:.3f} ask={quotes.ask:.3f} "
            f"spread={quotes.spread:.4f} inv={pos.net_inventory():.1f}"
        )

    def _cancel_open_orders(self, info: MarketInfo) -> None:
        pos = self.inventory.get(info.yes_token_id)
        for oid in [pos.open_bid_id, pos.open_ask_id]:
            if oid:
                self.client.cancel_order(oid)
        pos.open_bid_id = None
        pos.open_ask_id = None

    def record_fill(
        self,
        token_id: str,
        side: str,
        price: float,
        shares: float,
    ) -> None:
        """Called when a fill is confirmed (from user WebSocket or polling)."""
        usdc = price * shares
        self.inventory.get(token_id).record_fill(side, price, shares, usdc)

    def engine_state(self, token_id: str) -> MarketState | None:
        return self._markets.get(token_id)

    def summary(self) -> dict:
        mids = {
            tid: s.info.mid
            for tid, s in self._markets.items()
        }
        return {
            "markets": len(self._markets),
            "active": sum(1 for s in self._markets.values() if s.active),
            "total_pnl_usdc": round(self.inventory.total_pnl(mids), 4),
            "total_fills": sum(
                self.inventory.get(tid).fills
                for tid in self._markets
            ),
        }
