"""
PolyMaker — main entry point.

Wires together:
  BookFeed (WebSocket) → MakerEngine → PolymakerClient (CLOB)

Usage:
  python main.py                    # run with top markets
  python main.py --markets 3        # run on top N markets
  python main.py --sim              # simulation mode (no real orders)
  python main.py --list-markets     # show ranked markets and exit
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import os
import signal

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table

from book_feed import BookFeed
from clob_client import PolymakerClient
from engine import MakerEngine
from inventory import InventoryManager
from market_selector import fetch_candidate_markets

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("polymaker")
console = Console()


def build_status_table(engine: MakerEngine, markets) -> Table:
    summary = engine.summary()
    table = Table(title="PolyMaker Status", show_header=True)
    table.add_column("Market", max_width=45)
    table.add_column("Category")
    table.add_column("Mid")
    table.add_column("Bid")
    table.add_column("Ask")
    table.add_column("Inv")
    table.add_column("PnL")
    table.add_column("Fills")

    for m in markets:
        pos = engine.inventory.get(m.yes_token_id)
        state = engine.engine_state(m.yes_token_id)
        pnl = pos.total_pnl(m.mid)
        pnl_str = f"[green]+{pnl:.2f}[/green]" if pnl >= 0 else f"[red]{pnl:.2f}[/red]"
        table.add_row(
            m.question[:44],
            m.category.value,
            f"{m.mid:.3f}",
            f"{state.last_bid_price:.3f}" if state else "-",
            f"{state.last_ask_price:.3f}" if state else "-",
            f"{pos.net_inventory():.1f}",
            pnl_str,
            str(pos.fills),
        )

    table.caption = (
        f"Total PnL: {summary['total_pnl_usdc']:+.4f} USDC | "
        f"Active: {summary['active']}/{summary['markets']} | "
        f"Fills: {summary['total_fills']}"
    )
    return table


async def run(n_markets: int, sim: bool) -> None:
    console.print("[bold green]PolyMaker[/bold green] — Fee-aware Polymarket market maker")

    # Fetch and rank markets
    console.print("Fetching candidate markets...")
    markets = fetch_candidate_markets(limit=50)[:n_markets]
    if not markets:
        console.print("[red]No suitable markets found.[/red]")
        return

    console.print(f"Selected {len(markets)} markets:")
    for m in markets:
        console.print(
            f"  [{m.category.value}] {m.question[:60]} "
            f"mid={m.mid:.3f} vol24h=${m.volume_24h:,.0f}"
        )

    # Initialize components
    client = PolymakerClient()
    if sim or client.is_simulation:
        console.print("[yellow]Running in SIMULATION mode — no real orders placed[/yellow]")

    inventory = InventoryManager(
        max_position_usdc=float(os.getenv("MAX_POSITION_USDC", "50.0")),
        stop_loss_usdc=float(os.getenv("STOP_LOSS_USDC", "-10.0")),
    )
    engine = MakerEngine(
        client=client,
        inventory=inventory,
        risk_aversion=float(os.getenv("RISK_AVERSION", "0.1")),
        order_arrival=10.0,
    )
    for m in markets:
        engine.add_market(m)

    token_ids = [m.yes_token_id for m in markets]
    tick_sizes = {m.yes_token_id: m.tick_size for m in markets}

    feed = BookFeed(
        token_ids=token_ids,
        on_update=engine.on_book_update,
        initial_tick_sizes=tick_sizes,
    )

    # Graceful shutdown
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _shutdown():
        console.print("\n[yellow]Shutting down — cancelling all orders...[/yellow]")
        for m in markets:
            engine._cancel_open_orders(m)
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, _shutdown)
    loop.add_signal_handler(signal.SIGTERM, _shutdown)

    # Run feed + engine concurrently
    async def _run_until_stop():
        feed_task = asyncio.create_task(feed.run())
        engine_task = asyncio.create_task(engine.run())
        await stop_event.wait()
        feed_task.cancel()
        engine_task.cancel()

    with Live(build_status_table(engine, markets), refresh_per_second=1, console=console) as live:
        async def _refresh():
            while not stop_event.is_set():
                live.update(build_status_table(engine, markets))
                await asyncio.sleep(1)

        await asyncio.gather(
            _run_until_stop(),
            _refresh(),
            return_exceptions=True,
        )

    console.print(f"\nFinal: {engine.summary()}")


def main():
    parser = argparse.ArgumentParser(description="PolyMaker — Polymarket market maker")
    parser.add_argument("--markets", type=int, default=3, help="Number of markets to make")
    parser.add_argument("--sim", action="store_true", help="Simulation mode")
    parser.add_argument("--list-markets", action="store_true", help="List ranked markets and exit")
    args = parser.parse_args()

    if args.list_markets:
        markets = fetch_candidate_markets(limit=20)
        table = Table(title="Candidate Markets (ranked by MM score)")
        table.add_column("#")
        table.add_column("Question", max_width=50)
        table.add_column("Category")
        table.add_column("Mid")
        table.add_column("Spread")
        table.add_column("Vol 24h")
        table.add_column("Score")
        for i, m in enumerate(markets, 1):
            table.add_row(
                str(i), m.question[:49], m.category.value,
                f"{m.mid:.3f}", f"{m.spread:.3f}",
                f"${m.volume_24h:,.0f}", f"{m.score:.4f}",
            )
        console.print(table)
        return

    asyncio.run(run(args.markets, args.sim))


if __name__ == "__main__":
    main()
