"""
Polymarket CLOB client wrapper using py-clob-client-v2.

Handles:
  - API key initialization
  - Order placement with builder code
  - Order cancellation (single + batch)
  - Open order queries
  - Rebate queries

Builder code: attached to every order → earns fees on every fill.
Get yours at: https://polymarket.com/settings?tab=builder
"""
from __future__ import annotations
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# py-clob-client-v2 mirrors the old API with V2 order struct support
try:
    from py_clob_client_v2 import ClobClient, ApiCreds
    from py_clob_client_v2.clob_types import OrderArgs, OrderType, Side
    _CLIENT_AVAILABLE = True
except ImportError:
    _CLIENT_AVAILABLE = False
    logger.warning("py-clob-client-v2 not installed — running in simulation mode")


CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polymarket runs on Polygon


class PolymakerClient:
    """
    Thin wrapper around py-clob-client-v2.
    Falls back to simulation mode if SDK not installed.
    """

    def __init__(self):
        self.builder_code = os.getenv("POLYMARKET_BUILDER_CODE", "")
        self._sim = not _CLIENT_AVAILABLE

        if not self._sim:
            self._client = ClobClient(
                host=CLOB_HOST,
                chain_id=CHAIN_ID,
                key=os.environ["POLYMARKET_PRIVATE_KEY"],
                creds=ApiCreds(
                    api_key=os.environ["POLYMARKET_API_KEY"],
                    api_secret=os.environ["POLYMARKET_API_SECRET"],
                    api_passphrase=os.environ["POLYMARKET_API_PASSPHRASE"],
                ),
                signature_type=0,
                funder=os.getenv("POLYMARKET_WALLET_ADDRESS"),
            )
        else:
            self._client = None
            self._sim_orders: dict[str, dict] = {}
            self._sim_counter = 0

    # ── Order placement ────────────────────────────────────────────────────

    def place_limit_order(
        self,
        token_id: str,
        side: str,          # "BUY" or "SELL"
        price: float,
        size: float,
        order_type: str = "GTC",
    ) -> dict[str, Any]:
        """
        Place a limit order with builder code attached.
        Returns {"order_id": str, "status": str}.
        """
        if self._sim:
            return self._sim_place(token_id, side, price, size)

        args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=Side.BUY if side == "BUY" else Side.SELL,
        )
        otype = OrderType.GTC if order_type == "GTC" else OrderType.FOK

        # Attach builder code to every order
        resp = self._client.create_and_post_order(
            args,
            order_type=otype,
            builder_code=self.builder_code or None,
        )
        return {"order_id": resp.get("orderID", ""), "status": resp.get("status", "")}

    def cancel_order(self, order_id: str) -> bool:
        if self._sim:
            self._sim_orders.pop(order_id, None)
            return True
        try:
            self._client.cancel(order_id)
            return True
        except Exception as e:
            logger.warning(f"Cancel failed {order_id}: {e}")
            return False

    def cancel_all_for_market(self, condition_id: str) -> bool:
        if self._sim:
            return True
        try:
            self._client.cancel_market_orders(market=condition_id)
            return True
        except Exception as e:
            logger.warning(f"Cancel market orders failed {condition_id}: {e}")
            return False

    def get_open_orders(self, maker_address: str | None = None) -> list[dict]:
        if self._sim:
            return list(self._sim_orders.values())
        try:
            return self._client.get_orders(
                maker_address=maker_address or os.getenv("POLYMARKET_WALLET_ADDRESS", "")
            ) or []
        except Exception as e:
            logger.warning(f"get_open_orders failed: {e}")
            return []

    def get_rebates(self, maker_address: str) -> dict:
        """Query current rebate accrual."""
        if self._sim:
            return {"rebate_usdc": 0.0}
        try:
            import requests
            r = requests.get(
                f"{CLOB_HOST}/rebates/current",
                params={"maker_address": maker_address},
                timeout=5,
            )
            return r.json() if r.status_code == 200 else {}
        except Exception:
            return {}

    # ── Simulation helpers ─────────────────────────────────────────────────

    def _sim_place(self, token_id: str, side: str, price: float, size: float) -> dict:
        self._sim_counter += 1
        oid = f"sim-{self._sim_counter:06d}"
        self._sim_orders[oid] = {
            "order_id": oid, "token_id": token_id,
            "side": side, "price": price, "size": size, "status": "LIVE",
        }
        logger.debug(f"[SIM] Placed {side} {size}@{price} → {oid}")
        return {"order_id": oid, "status": "LIVE"}

    @property
    def is_simulation(self) -> bool:
        return self._sim
