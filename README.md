# PolyMaker

**Fee-aware autonomous market maker for Polymarket, with Circle Arc wallet management**

[![Tests](https://img.shields.io/badge/tests-23%20passing-green)](tests/)
[![Polymarket](https://img.shields.io/badge/Polymarket-V2%20CLOB-blue)](https://polymarket.com)
[![Arc](https://img.shields.io/badge/Arc-testnet-purple)](https://arc.network)
[![Builder](https://img.shields.io/badge/builder%20code-enabled-orange)](https://polymarket.com/settings?tab=builder)

PolyMaker is an autonomous market making bot for [Polymarket](https://polymarket.com) that continuously quotes bid and ask prices across multiple prediction markets simultaneously. It earns the maker rebate on every taker fill, attaches a Polymarket V2 builder code to every order so it also earns builder fees, and manages user deposits through Circle developer-controlled wallets on Arc testnet.

It is the only open-source Polymarket MM bot updated for the **March 2026 fee structure**. Every other bot in the wild uses a hardcoded or wrong fee formula, which means they cannot correctly compute whether a given spread is profitable.

---

## What problem this solves

Polymarket introduced a new fee formula in March 2026. The formula is:

```
taker_fee = C x feeRate x p x (1 - p)^exponent
```

The exponent varies by market category (1 for most, 0.5 for Economics/Weather, 2 for Other/General). No existing open-source bot handles this correctly. A market maker quoting without knowing the true fee cost cannot know whether their spread covers adverse selection. PolyMaker computes the fee-aware minimum spread per market before placing any order.

The second problem: existing bots use symmetric fixed spreads. PolyMaker uses the Avellaneda-Stoikov model adapted for prediction markets, which skews quotes based on current inventory. When the bot is long YES shares, it lowers its bid (less eager to buy more) and raises its ask (more eager to sell). This reduces adverse selection risk and keeps inventory near zero over time.

---

## How it works

```
Polymarket WebSocket
  (real-time order book)
        |
        v
    BookFeed
  (maintains live bid/ask levels for each market)
        |
        v
    MakerEngine
  (event-driven: every book update triggers a requote check)
        |
        +-- quoting.py: Avellaneda-Stoikov in logit space
        |   reservation_price = sigmoid(logit(mid) - q x gamma x sigma^2 x T)
        |   bid = sigmoid(reservation - half_spread)
        |   ask = sigmoid(reservation + half_spread)
        |
        +-- fees.py: fee-aware minimum spread per category
        |   min_half_spread = feeRate x p x (1-p)^exp x 0.5
        |
        +-- inventory.py: position tracking, VWAP, stop-loss
        |
        v
    PolymakerClient
  (py-clob-client-v2, builder_code on every order)
        |
        v
    Circle Arc Wallets
  (developer-controlled, one wallet per user, Arc testnet chain 5042002)
```

The engine is event-driven. Book updates arrive over WebSocket and are enqueued for requoting only if the price moved more than 0.5% or there are no open orders. A 2-second minimum interval between requotes per market prevents order spam. When a market resolves, the engine cancels all open orders and stops quoting that market.

---

## Fee model (post March 30, 2026)

| Category | Fee rate | Exponent | Peak effective fee |
|---|---|---|---|
| Crypto | 7% | 1 | 1.80% |
| Sports | 3% | 1 | 0.75% |
| Finance / Politics | 4% | 1 | 1.00% |
| Economics / Weather | 5% | 0.5 | 1.25% |
| Other / General | 5% | 2 | 1.25% |
| Geopolitics | 0% | n/a | 0% (skipped) |

Makers never pay fees. The maker rebate is paid daily in USDC, weighted by your fee-equivalent volume in the pool. Geopolitics markets are skipped entirely because there is no rebate pool.

---

## Quoting model

Standard bots place symmetric quotes at `mid +/- fixed_spread`. PolyMaker uses the Avellaneda-Stoikov model, adapted to work in logit space because prediction market prices are bounded in [0, 1].

Working in probability space directly causes problems: a spread of 0.02 at mid=0.5 is very different from a spread of 0.02 at mid=0.05. Logit space (`log(p / (1-p))`) linearizes this, so the A-S half-spread formula applies uniformly across the full price range.

```python
# Logit-space reservation price (inventory-skewed)
q = inventory / max_inventory          # normalized, range [-1, 1]
reservation = logit(mid) - q * gamma * sigma_sq * time_to_expiry

# Optimal half-spread
half_spread = gamma * sigma_sq * T / 2 + log(1 + gamma/kappa) / gamma

# Convert back to probability space
bid = sigmoid(reservation - half_spread)
ask = sigmoid(reservation + half_spread)

# Enforce fee-aware minimum
bid = min(bid, mid - min_half_spread)
ask = max(ask, mid + min_half_spread)
```

Volatility (`sigma_sq`) is estimated from the last 20 mid-price observations in logit space, updated on every book event.

Reference: [arXiv:2510.15205](https://arxiv.org/abs/2510.15205) "Toward Black-Scholes for Prediction Markets"

---

## Circle Arc integration

PolyMaker uses Circle developer-controlled wallets on Arc testnet (chain ID 5042002) to manage user deposits.

| Circle tool | How it is used |
|---|---|
| Developer-Controlled Wallets | One Arc testnet wallet created per user via POST /wallets |
| Arc testnet | USDC-native chain, no ETH needed for gas |
| USDC | Native settlement currency; maker rebates paid in USDC |
| Builder codes | Attached to every Polymarket order; earns fees on every taker fill |

The wallet manager (`circle_wallets.py`) creates wallets, queries USDC balances, and handles USDC transfers. It falls back to a full in-memory simulation if the Circle SDK is not installed, so the bot runs end-to-end in simulation mode with no credentials required.

---

## Market selection

The bot fetches active Polymarket CLOB markets from the Gamma API and ranks them by a profitability score:

```
score = spread_score x volume_score x fee_score
```

Markets are filtered out if:
- Mid-price is outside [0.10, 0.90] (extreme probabilities have 1300-1800bps spreads per arXiv:2604.24366, making them unprofitable to quote)
- 24h volume is below $1,000
- Liquidity is below $500
- Category is Geopolitics (zero fee, no rebate)

---

## Quick start

```bash
git clone https://github.com/Tasfia-17/Polymaker
cd Polymaker
cp .env.example .env
# Edit .env: add POLYMARKET_PRIVATE_KEY, API keys, POLYMARKET_BUILDER_CODE

pip install uv && uv sync

# See ranked markets without running the bot
python main.py --list-markets

# Run in simulation mode (no real orders placed)
python main.py --sim --markets 3

# Run live on 3 markets
python main.py --markets 3
```

### REST API and dashboard

```bash
# Start the API
uvicorn api:app --port 8000

# Open the landing page
open index.html

# Open the dashboard
open dashboard.html
```

API endpoints:

```bash
# Create a Circle Arc wallet for a user
curl -X POST http://localhost:8000/wallets \
  -H "Content-Type: application/json" \
  -d '{"user_ref": "alice"}'

# Check wallet balance
curl http://localhost:8000/wallets/alice

# Get ranked candidate markets
curl http://localhost:8000/markets?limit=10

# Engine status (PnL, fills, active markets)
curl http://localhost:8000/status
```

---

## Tests

```bash
python -m pytest tests/ -v
```

23 tests covering the fee model, A-S quoting, and inventory P&L. All pure-logic modules with no network calls.

---

## Project structure

```
polymaker/
├── main.py              # CLI entry point, Rich live dashboard
├── engine.py            # Event-driven market making loop
├── book_feed.py         # WebSocket order book state machine
├── quoting.py           # Avellaneda-Stoikov in logit space
├── fees.py              # 2026 fee formula, per-category exponents
├── inventory.py         # Position tracking, VWAP, P&L, stop-loss
├── market_selector.py   # Fetch and rank markets from Gamma API
├── clob_client.py       # py-clob-client-v2 wrapper, builder code
├── circle_wallets.py    # Circle developer-controlled wallets on Arc
├── api.py               # FastAPI REST interface
├── index.html           # Landing page
├── dashboard.html       # Live app dashboard
├── tests/
│   └── test_core.py     # 23 unit tests
└── .env.example
```

---

## Environment variables

```bash
# Polymarket credentials
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_API_PASSPHRASE=...
POLYMARKET_BUILDER_CODE=0x...   # from polymarket.com/settings?tab=builder

# Circle Arc wallets
CIRCLE_API_KEY=...
CIRCLE_ENTITY_SECRET=...        # 32-byte hex

# Strategy parameters
MAX_POSITION_USDC=50.0          # max USDC deployed per market
STOP_LOSS_USDC=-10.0            # exit market if unrealized PnL drops below this
RISK_AVERSION=0.1               # gamma: higher = wider quotes
ORDER_SIZE_USDC=5.0             # USDC per side per order
```

---

## References

- [Avellaneda and Stoikov (2008)](https://math.nyu.edu/~avellane/HighFrequencyTrading.pdf) -- original A-S market making model
- [arXiv:2510.15205](https://arxiv.org/abs/2510.15205) -- Black-Scholes for prediction markets, logit-space adaptation
- [arXiv:2604.24366](https://arxiv.org/abs/2604.24366) -- Polymarket microstructure, tick size, adverse selection at extremes
- [Polymarket fee docs](https://docs.polymarket.com/trading/fees) -- exact fee formula and category breakdown
- [Circle developer docs](https://developers.circle.com) -- developer-controlled wallets, Arc testnet
