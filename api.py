"""
REST API — user-facing interface for PolyMaker.

Endpoints:
  POST /wallets          — create Arc wallet for a user
  GET  /wallets/{id}     — get wallet balance
  GET  /status           — engine status (markets, PnL, fills)
  GET  /markets          — ranked candidate markets
  GET  /health           — liveness check

Run: uvicorn api:app --port 8000
"""
from __future__ import annotations
import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from circle_wallets import CircleWalletManager, WalletInfo
from gateway_bridge import GatewayBridge
from market_selector import fetch_candidate_markets

app = FastAPI(
    title="PolyMaker API",
    description="Fee-aware Polymarket market maker with Circle Arc wallets",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_wallet_manager = CircleWalletManager()
_bridge = GatewayBridge()
_wallets: dict[str, WalletInfo] = {}  # user_ref -> WalletInfo


class CreateWalletRequest(BaseModel):
    user_ref: str   # arbitrary user identifier


class CreateWalletResponse(BaseModel):
    wallet_id: str
    address: str
    blockchain: str
    usdc_balance: float


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/wallets", response_model=CreateWalletResponse)
async def create_wallet(req: CreateWalletRequest):
    """Create a Circle developer-controlled wallet on Arc testnet for a user."""
    if req.user_ref in _wallets:
        w = _wallets[req.user_ref]
    else:
        try:
            w = _wallet_manager.create_wallet(req.user_ref)
            _wallets[req.user_ref] = w
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    balance = _wallet_manager.get_usdc_balance(w.wallet_id)
    return CreateWalletResponse(
        wallet_id=w.wallet_id,
        address=w.address,
        blockchain=w.blockchain,
        usdc_balance=balance,
    )


@app.get("/wallets/{user_ref}")
async def get_wallet(user_ref: str):
    w = _wallets.get(user_ref)
    if not w:
        raise HTTPException(status_code=404, detail="Wallet not found")
    balance = _wallet_manager.get_usdc_balance(w.wallet_id)
    return {"wallet_id": w.wallet_id, "address": w.address, "usdc_balance": balance}


@app.get("/markets")
async def list_markets(limit: int = 10):
    """Return top ranked markets for market making."""
    markets = fetch_candidate_markets(limit=50)[:limit]
    return [
        {
            "question": m.question,
            "category": m.category.value,
            "mid": m.mid,
            "spread": m.spread,
            "volume_24h": m.volume_24h,
            "score": m.score,
            "yes_token_id": m.yes_token_id,
        }
        for m in markets
    ]


@app.get("/status")
async def status():
    """Engine status — injected by main.py at startup."""
    engine = app.state.engine if hasattr(app.state, "engine") else None
    if not engine:
        return {"status": "not_running"}
    return engine.summary()


class BridgeRequest(BaseModel):
    user_ref: str           # must have a wallet created via POST /wallets
    amount_usdc: float      # USDC to bridge from Polygon to Arc
    source_wallet_id: str   # Circle wallet ID on Polygon holding the USDC


class BridgeResponse(BaseModel):
    transfer_id: str
    from_chain: str
    to_chain: str
    amount_usdc: float
    destination_address: str
    status: str
    simulated: bool


@app.post("/bridge", response_model=BridgeResponse)
async def bridge_usdc(req: BridgeRequest):
    """
    Bridge USDC from Polygon to the user's Arc testnet wallet via Circle Gateway.
    The user must have an Arc wallet created first via POST /wallets.
    """
    w = _wallets.get(req.user_ref)
    if not w:
        raise HTTPException(status_code=404, detail="Arc wallet not found. Call POST /wallets first.")
    try:
        result = _bridge.bridge_to_arc(
            source_wallet_id=req.source_wallet_id,
            destination_arc_address=w.address,
            amount_usdc=req.amount_usdc,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return BridgeResponse(
        transfer_id=result.transfer_id,
        from_chain=result.from_chain,
        to_chain=result.to_chain,
        amount_usdc=result.amount_usdc,
        destination_address=result.destination_address,
        status=result.status,
        simulated=result.simulated,
    )


@app.get("/bridge/{transfer_id}")
async def bridge_status(transfer_id: str):
    """Poll the status of a cross-chain USDC transfer."""
    return {"transfer_id": transfer_id, "status": _bridge.get_transfer_status(transfer_id)}
