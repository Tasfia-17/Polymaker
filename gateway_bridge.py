"""
Circle Gateway cross-chain USDC bridge: Polygon -> Arc testnet.

Circle Gateway enables sub-500ms cross-chain USDC transfers with a
unified balance across chains. This module lets users deposit USDC
from Polygon (where Polymarket lives) and receive it on Arc testnet
(where PolyMaker wallets live) in a single API call.

Arc testnet:
  Chain ID:  5042002
  USDC:      0x3600000000000000000000000000000000000000

Polygon:
  Chain ID:  137
  USDC:      0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359

Gateway docs: https://developers.circle.com/circle-mint/docs/gateway
"""
from __future__ import annotations
import logging
import os
import uuid
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

GATEWAY_API = "https://api.circle.com/v1/w3s"
POLYGON_CHAIN = "MATIC"          # Circle's chain identifier for Polygon
ARC_CHAIN = "ARC-TESTNET"
ARC_USDC = "0x3600000000000000000000000000000000000000"
POLYGON_USDC = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"


@dataclass
class BridgeResult:
    transfer_id: str
    from_chain: str
    to_chain: str
    amount_usdc: float
    destination_address: str
    status: str          # "pending" | "complete" | "failed"
    simulated: bool = False


class GatewayBridge:
    """
    Bridges USDC from Polygon to Arc testnet using Circle Gateway.

    Falls back to simulation if CIRCLE_API_KEY is not set.
    """

    def __init__(self):
        self._api_key = os.getenv("CIRCLE_API_KEY", "")
        self._entity_secret = os.getenv("CIRCLE_ENTITY_SECRET", "")
        self._sim = not bool(self._api_key)
        if self._sim:
            logger.info("GatewayBridge: no CIRCLE_API_KEY, running in simulation mode")

    def bridge_to_arc(
        self,
        source_wallet_id: str,
        destination_arc_address: str,
        amount_usdc: float,
    ) -> BridgeResult:
        """
        Transfer USDC from a Polygon wallet to an Arc testnet address.

        Args:
            source_wallet_id: Circle wallet ID holding USDC on Polygon
            destination_arc_address: Arc testnet wallet address to receive USDC
            amount_usdc: Amount of USDC to bridge

        Returns:
            BridgeResult with transfer ID and status
        """
        if self._sim:
            return self._sim_bridge(destination_arc_address, amount_usdc)

        try:
            return self._live_bridge(source_wallet_id, destination_arc_address, amount_usdc)
        except Exception as e:
            logger.error(f"GatewayBridge failed: {e}")
            raise

    def _live_bridge(
        self,
        source_wallet_id: str,
        destination_arc_address: str,
        amount_usdc: float,
    ) -> BridgeResult:
        """Execute a real cross-chain transfer via Circle Gateway."""
        idempotency_key = str(uuid.uuid4())

        resp = requests.post(
            f"{GATEWAY_API}/transactions/transfer",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "idempotencyKey": idempotency_key,
                "walletId": source_wallet_id,
                "tokenAddress": POLYGON_USDC,
                "blockchain": POLYGON_CHAIN,
                "destinationAddress": destination_arc_address,
                "destinationBlockchain": ARC_CHAIN,
                "amounts": [str(amount_usdc)],
                "feeLevel": "MEDIUM",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        transfer_id = data.get("id", idempotency_key)

        logger.info(
            f"GatewayBridge: initiated ${amount_usdc} USDC "
            f"Polygon -> Arc | transfer_id={transfer_id}"
        )
        return BridgeResult(
            transfer_id=transfer_id,
            from_chain=POLYGON_CHAIN,
            to_chain=ARC_CHAIN,
            amount_usdc=amount_usdc,
            destination_address=destination_arc_address,
            status="pending",
        )

    def get_transfer_status(self, transfer_id: str) -> str:
        """Poll transfer status. Returns 'pending' | 'complete' | 'failed'."""
        if self._sim:
            return "complete"
        try:
            resp = requests.get(
                f"{GATEWAY_API}/transactions/{transfer_id}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            state = resp.json().get("data", {}).get("transaction", {}).get("state", "")
            mapping = {"CONFIRMED": "complete", "FAILED": "failed"}
            return mapping.get(state, "pending")
        except Exception as e:
            logger.warning(f"get_transfer_status failed: {e}")
            return "pending"

    def _sim_bridge(self, destination: str, amount_usdc: float) -> BridgeResult:
        tid = f"sim-bridge-{uuid.uuid4().hex[:8]}"
        logger.info(f"[SIM] Bridge ${amount_usdc} USDC Polygon -> Arc ({destination}): {tid}")
        return BridgeResult(
            transfer_id=tid,
            from_chain=POLYGON_CHAIN,
            to_chain=ARC_CHAIN,
            amount_usdc=amount_usdc,
            destination_address=destination,
            status="complete",
            simulated=True,
        )
