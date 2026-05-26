"""
Real-time order book state maintained from WebSocket feed.

Uses the Polymarket market WebSocket channel:
  wss://ws-subscriptions-clob.polymarket.com/ws/market

Key finding from arXiv:2604.24366: the `change_side` field in price_change
events marks which side of the BOOK moved, NOT which side initiated the trade.
Do NOT use it for aggressor inference.
"""
from __future__ import annotations
import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import websockets

logger = logging.getLogger(__name__)

WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass
class Level:
    price: float
    size: float


@dataclass
class OrderBook:
    token_id: str
    bids: list[Level] = field(default_factory=list)   # sorted desc
    asks: list[Level] = field(default_factory=list)   # sorted asc
    last_trade_price: float | None = None
    tick_size: float = 0.01
    resolved: bool = False
    mid_history: deque = field(default_factory=lambda: deque(maxlen=100))

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2.0
        return self.last_trade_price

    def apply_snapshot(self, bids: list[dict], asks: list[dict]) -> None:
        self.bids = sorted(
            [Level(float(b["price"]), float(b["size"])) for b in bids if float(b["size"]) > 0],
            key=lambda x: -x.price,
        )
        self.asks = sorted(
            [Level(float(a["price"]), float(a["size"])) for a in asks if float(a["size"]) > 0],
            key=lambda x: x.price,
        )
        if m := self.mid:
            self.mid_history.append(m)

    def apply_price_change(self, changes: list[dict]) -> None:
        for c in changes:
            price = float(c["price"])
            size = float(c["size"])
            side = c.get("side", "").upper()
            levels = self.bids if side == "BUY" else self.asks
            reverse = side == "BUY"
            levels[:] = [l for l in levels if l.price != price]
            if size > 0:
                levels.append(Level(price, size))
                levels.sort(key=lambda x: -x.price if reverse else x.price)
        if m := self.mid:
            self.mid_history.append(m)


class BookFeed:
    """
    Manages WebSocket connection and dispatches book updates.
    Calls on_update(token_id, book) whenever the book changes.
    """

    def __init__(
        self,
        token_ids: list[str],
        on_update: Callable[[str, OrderBook], None],
        initial_tick_sizes: dict[str, float] | None = None,
    ):
        self.token_ids = token_ids
        self.on_update = on_update
        self.books: dict[str, OrderBook] = {
            tid: OrderBook(
                token_id=tid,
                tick_size=(initial_tick_sizes or {}).get(tid, 0.01),
            )
            for tid in token_ids
        }

    async def run(self) -> None:
        while True:
            try:
                await self._connect()
            except Exception as e:
                logger.warning(f"BookFeed disconnected: {e}. Reconnecting in 3s...")
                await asyncio.sleep(3)

    async def _connect(self) -> None:
        async with websockets.connect(
            WS_MARKET_URL,
            ping_interval=10,
            ping_timeout=20,
        ) as ws:
            await ws.send(json.dumps({
                "type": "market",
                "assets_ids": self.token_ids,
                "custom_feature_enabled": True,  # enables best_bid_ask + market_resolved
            }))
            logger.info(f"BookFeed subscribed to {len(self.token_ids)} tokens")
            async for raw in ws:
                self._handle(json.loads(raw))

    def _handle(self, msg: dict | list) -> None:
        events = msg if isinstance(msg, list) else [msg]
        for event in events:
            etype = event.get("event_type", "")
            tid = event.get("asset_id", "")
            book = self.books.get(tid)
            if not book:
                continue

            if etype == "book":
                book.apply_snapshot(event.get("bids", []), event.get("asks", []))
                self.on_update(tid, book)

            elif etype == "price_change":
                book.apply_price_change(event.get("price_changes", []))
                self.on_update(tid, book)

            elif etype == "last_trade_price":
                book.last_trade_price = float(event.get("price", 0))
                self.on_update(tid, book)

            elif etype == "tick_size_change":
                # CRITICAL: update immediately or orders will be rejected
                new_tick = float(event.get("new_tick_size", book.tick_size))
                logger.info(f"Tick size change {tid}: {book.tick_size} → {new_tick}")
                book.tick_size = new_tick
                self.on_update(tid, book)

            elif etype == "best_bid_ask":
                bb, ba = event.get("best_bid"), event.get("best_ask")
                if bb and book.bids:
                    book.bids[0].price = float(bb)
                if ba and book.asks:
                    book.asks[0].price = float(ba)
                if m := book.mid:
                    book.mid_history.append(m)
                self.on_update(tid, book)

            elif etype == "market_resolved":
                logger.info(f"Market resolved: {tid}")
                book.resolved = True
                self.on_update(tid, book)
