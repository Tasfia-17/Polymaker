"""
Inventory and P&L tracker per market.

Tracks:
  - YES/NO share inventory
  - Average entry price (VWAP)
  - Realized and unrealized P&L
  - Open order IDs (to cancel on requote)

Stop-loss: if unrealized_pnl < stop_loss_usdc, signal exit.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Position:
    token_id: str
    yes_shares: float = 0.0
    no_shares: float = 0.0
    avg_yes_price: float = 0.0
    avg_no_price: float = 0.0
    realized_pnl: float = 0.0
    usdc_spent: float = 0.0
    open_bid_id: str | None = None   # current live bid order ID
    open_ask_id: str | None = None   # current live ask order ID
    fills: int = 0
    maker_rebate_earned: float = 0.0

    def record_fill(
        self,
        side: str,       # "BUY" or "SELL"
        price: float,
        shares: float,
        usdc: float,
    ) -> None:
        """Update inventory on a fill. side = side of the aggressor (taker)."""
        if side == "BUY":
            # Taker bought from us → we sold YES (our ask was hit)
            if self.yes_shares > 0:
                realized = (price - self.avg_yes_price) * shares
                self.realized_pnl += realized
            self.yes_shares -= shares
            self.usdc_spent -= usdc  # we received USDC
        else:
            # Taker sold to us → we bought YES (our bid was hit)
            total_shares = self.yes_shares + shares
            if total_shares > 0:
                self.avg_yes_price = (
                    self.avg_yes_price * self.yes_shares + price * shares
                ) / total_shares
            self.yes_shares += shares
            self.usdc_spent += usdc
        self.fills += 1

    def unrealized_pnl(self, current_mid: float) -> float:
        """Mark-to-market unrealized P&L."""
        return self.yes_shares * (current_mid - self.avg_yes_price)

    def total_pnl(self, current_mid: float) -> float:
        return self.realized_pnl + self.unrealized_pnl(current_mid)

    def should_stop(self, current_mid: float, stop_loss_usdc: float) -> bool:
        return self.unrealized_pnl(current_mid) < stop_loss_usdc

    def net_inventory(self) -> float:
        """Signed inventory: positive = net long YES."""
        return self.yes_shares - self.no_shares


class InventoryManager:
    def __init__(self, max_position_usdc: float, stop_loss_usdc: float):
        self.max_position_usdc = max_position_usdc
        self.stop_loss_usdc = stop_loss_usdc
        self.positions: dict[str, Position] = {}

    def get(self, token_id: str) -> Position:
        if token_id not in self.positions:
            self.positions[token_id] = Position(token_id=token_id)
        return self.positions[token_id]

    def max_shares_to_buy(self, token_id: str, price: float) -> float:
        """How many more YES shares can we buy without exceeding position limit."""
        pos = self.get(token_id)
        usdc_deployed = pos.yes_shares * pos.avg_yes_price
        remaining = self.max_position_usdc - usdc_deployed
        if remaining <= 0 or price <= 0:
            return 0.0
        return remaining / price

    def total_pnl(self, mids: dict[str, float]) -> float:
        return sum(
            p.total_pnl(mids.get(tid, p.avg_yes_price))
            for tid, p in self.positions.items()
        )
