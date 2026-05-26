# PolyMaker

**Fee-aware Polymarket market maker with Circle Arc wallet management**

[![Tests](https://img.shields.io/badge/tests-23%20passing-green)](tests/)
[![Polymarket](https://img.shields.io/badge/Polymarket-V2%20CLOB-blue)](https://polymarket.com)
[![Arc](https://img.shields.io/badge/Arc-testnet-purple)](https://arc.network)
[![Builder](https://img.shields.io/badge/builder%20code-enabled-orange)](https://polymarket.com/settings?tab=builder)

The only open-source Polymarket market maker updated for the **March 2026 fee structure**. Uses Avellaneda-Stoikov quoting in logit space, inventory-skewed quotes, and Circle developer-controlled wallets on Arc testnet.

---

## Why this exists

`warproxxx/poly-maker` (1.2k stars) was archived and its fee model is wrong. The January 2026 fee change broke every existing MM bot. This is the updated version:

| | poly-maker (archived) | PolyMaker |
|---|---|---|
| Fee formula | hardcoded / wrong | `C × feeRate × p × (1-p)^exp` per category |
| Quoting model | fixed spread | Avellaneda-Stoikov in logit space |
| Inventory skew | none | yes — reduces adverse selection |
| Builder code | no | yes — earns fees on every fill |
| Arc wallets | no | Circle developer-controlled wallets |
| SDK | py-clob-client (archived) | py-clob-client-v2 |

---

## Architecture

```
Polymarket WebSocket ──► BookFeed ──► MakerEngine
  (real-time book)         │              │
                           │         ┌────┴────────────────┐
                           │         │  Avellaneda-Stoikov  │
                           │         │  quotes (logit space)│
                           │         │  + fee-aware spread  │
                           │         │  + inventory skew    │
                           │         └────┬────────────────┘
                           │              │
                           └──────────────▼
                                   PolymakerClient
                                   (py-clob-client-v2)
                                   builder_code on every order
                                         │
                                   Circle Arc Wallets
                                   (developer-controlled)
```

---

## Fee Model (post March 30, 2026)

```
taker_fee = C × feeRate × p × (1 - p)^exponent
```

| Category | feeRate | exponent | Peak effective fee |
|---|---|---|---|
| Crypto | 0.07 | 1 | 1.80% |
| Sports | 0.03 | 1 | 0.75% |
| Finance/Politics | 0.04 | 1 | 1.00% |
| Economics/Weather | 0.05 | 0.5 | 1.25% |
| Other/General | 0.05 | 2 | 1.25% |
| Geopolitics | 0 | — | 0% |

**Makers never pay fees.** Maker rebate paid daily in USDC (20-25% of taker fee pool, weighted by your fee-equivalent volume).

---

## Quoting Model

Avellaneda-Stoikov adapted for prediction markets (logit space):

```
reservation_logit = logit(mid) - q × γ × σ² × (T-t)
bid = sigmoid(reservation_logit - δ/2)
ask = sigmoid(reservation_logit + δ/2)
```

where `q` = normalized inventory, `γ` = risk aversion, `σ²` = logit-space variance from recent mid-price history.

Working in logit space respects the [0,1] bound of prediction market prices. Reference: arXiv:2510.15205.

---

## Quick Start

```bash
git clone https://github.com/your-username/polymaker
cd polymaker
cp .env.example .env
# Fill in POLYMARKET_PRIVATE_KEY, API keys, POLYMARKET_BUILDER_CODE

pip install uv && uv sync

# List ranked markets
python main.py --list-markets

# Run in simulation (no real orders)
python main.py --sim --markets 3

# Run live
python main.py --markets 3
```

### REST API

```bash
uvicorn api:app --port 8000

# Create Arc wallet for a user
curl -X POST http://localhost:8000/wallets -d '{"user_ref": "alice"}'

# Get ranked markets
curl http://localhost:8000/markets

# Engine status
curl http://localhost:8000/status
```

---

## Tests

```bash
python -m pytest tests/ -v
# 23 tests: fee model, A-S quoting, inventory P&L
```

---

## Project Structure

```
polymaker/
├── main.py              # Entry point + live dashboard
├── engine.py            # Market making loop (event-driven)
├── book_feed.py         # WebSocket order book feed
├── quoting.py           # Avellaneda-Stoikov in logit space
├── fees.py              # Exact 2026 fee formula per category
├── inventory.py         # Position tracking + P&L + stop-loss
├── market_selector.py   # Rank markets by MM profitability
├── clob_client.py       # py-clob-client-v2 wrapper + builder code
├── circle_wallets.py    # Circle developer-controlled wallets on Arc
├── api.py               # FastAPI REST interface
├── tests/
│   └── test_core.py     # 23 unit tests
└── .env.example
```

---

## Circle Tools

| Tool | Usage |
|---|---|
| Developer-Controlled Wallets | One Arc testnet wallet per user deposit |
| Arc testnet (chain 5042002) | USDC-as-gas, no ETH needed |
| USDC | Native settlement, maker rebates paid in USDC |
| Builder codes | Attached to every order — earns fees on every fill |

---

## References

- [Avellaneda & Stoikov (2008)](https://math.nyu.edu/~avellane/HighFrequencyTrading.pdf) — original A-S model
- [arXiv:2510.15205](https://arxiv.org/abs/2510.15205) — Black-Scholes for prediction markets (logit space)
- [arXiv:2604.24366](https://arxiv.org/abs/2604.24366) — Polymarket microstructure (tick size, adverse selection)
- [Polymarket fee docs](https://docs.polymarket.com/trading/fees) — exact fee formula
- [warproxxx/poly-maker](https://github.com/warproxxx/poly-maker) — original bot (archived, fee model outdated)
