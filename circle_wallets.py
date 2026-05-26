"""
Circle Developer-Controlled Wallets integration.

Creates and manages one Arc testnet wallet per user deposit.
Handles USDC balance queries and transfers.

Arc testnet:
  Chain ID:    5042002
  USDC:        0x3600000000000000000000000000000000000000
  RPC:         https://rpc.testnet.arc.network
  Explorer:    https://testnet.arcscan.app
  Faucet:      https://faucet.circle.com

Note: USYC has $100K minimum + allowlist requirement — not suitable for
retail deposits. We hold USDC and earn yield via Polymarket maker rebates.
"""
from __future__ import annotations
import logging
import os
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ARC_BLOCKCHAIN = "ARC-TESTNET"
ARC_USDC = "0x3600000000000000000000000000000000000000"

try:
    from circle.web3 import utils as circle_utils
    from circle.web3 import developer_controlled_wallets as dcw
    _CIRCLE_AVAILABLE = True
except ImportError:
    _CIRCLE_AVAILABLE = False
    logger.warning("circle-developer-controlled-wallets not installed — wallet ops simulated")


@dataclass
class WalletInfo:
    wallet_id: str
    address: str
    blockchain: str = ARC_BLOCKCHAIN


class CircleWalletManager:
    """
    Manages Circle developer-controlled wallets on Arc testnet.
    One wallet set per PolyMaker deployment; one wallet per user.
    """

    def __init__(self):
        self._sim = not _CIRCLE_AVAILABLE
        self._wallet_set_id: str | None = None
        self._sim_wallets: dict[str, WalletInfo] = {}
        self._sim_balances: dict[str, float] = {}

        if not self._sim:
            self._client = circle_utils.init_developer_controlled_wallets_client(
                api_key=os.environ["CIRCLE_API_KEY"],
                entity_secret=os.environ["CIRCLE_ENTITY_SECRET"],
            )

    def ensure_wallet_set(self, name: str = "PolyMaker") -> str:
        """Create wallet set if not exists. Returns wallet_set_id."""
        if self._sim:
            self._wallet_set_id = "sim-wallet-set"
            return self._wallet_set_id

        if self._wallet_set_id:
            return self._wallet_set_id

        resp = self._client.create_wallet_set(
            idempotency_key=str(uuid.uuid4()),
            name=name,
        )
        self._wallet_set_id = resp["data"]["walletSet"]["id"]
        logger.info(f"Created wallet set: {self._wallet_set_id}")
        return self._wallet_set_id

    def create_wallet(self, user_ref: str) -> WalletInfo:
        """Create a new Arc testnet wallet for a user."""
        if self._sim:
            wid = f"sim-wallet-{len(self._sim_wallets)}"
            addr = f"0x{'0' * 39}{len(self._sim_wallets)}"
            w = WalletInfo(wallet_id=wid, address=addr)
            self._sim_wallets[wid] = w
            self._sim_balances[wid] = 0.0
            logger.info(f"[SIM] Created wallet {wid} for {user_ref}")
            return w

        ws_id = self.ensure_wallet_set()
        resp = self._client.create_wallets(
            idempotency_key=str(uuid.uuid4()),
            wallet_set_id=ws_id,
            blockchains=[ARC_BLOCKCHAIN],
            count=1,
            account_type="EOA",
            metadata=[{"name": user_ref, "refId": user_ref}],
        )
        w_data = resp["data"]["wallets"][0]
        w = WalletInfo(wallet_id=w_data["id"], address=w_data["address"])
        logger.info(f"Created wallet {w.wallet_id} ({w.address}) for {user_ref}")
        return w

    def get_usdc_balance(self, wallet_id: str) -> float:
        """Returns USDC balance as float."""
        if self._sim:
            return self._sim_balances.get(wallet_id, 0.0)

        resp = self._client.get_wallet_token_balance(
            wallet_id=wallet_id,
            token_address=ARC_USDC,
        )
        balances = resp.get("data", {}).get("tokenBalances", [])
        if balances:
            return float(balances[0].get("amount", 0))
        return 0.0

    def transfer_usdc(
        self,
        from_wallet_id: str,
        to_address: str,
        amount_usdc: float,
    ) -> str:
        """Transfer USDC. Returns transaction ID."""
        if self._sim:
            self._sim_balances[from_wallet_id] = (
                self._sim_balances.get(from_wallet_id, 0.0) - amount_usdc
            )
            tx_id = f"sim-tx-{uuid.uuid4().hex[:8]}"
            logger.info(f"[SIM] Transfer ${amount_usdc} from {from_wallet_id} → {to_address}: {tx_id}")
            return tx_id

        resp = self._client.create_developer_transaction_transfer(
            idempotency_key=str(uuid.uuid4()),
            wallet_id=from_wallet_id,
            blockchain=ARC_BLOCKCHAIN,
            token_address=ARC_USDC,
            destination_address=to_address,
            amounts=[str(amount_usdc)],
            fee_level="MEDIUM",
        )
        return resp["data"]["id"]

    def sim_deposit(self, wallet_id: str, amount_usdc: float) -> None:
        """Simulation only: add USDC to a wallet."""
        if self._sim:
            self._sim_balances[wallet_id] = (
                self._sim_balances.get(wallet_id, 0.0) + amount_usdc
            )
