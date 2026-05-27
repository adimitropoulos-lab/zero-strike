"""Zero-Strike CLI — one entry point for every capability.

Commands:
  wallets      Pull top wallets over a window and print P&L leaderboard.
  traders      Score wallets on the 4-axis edge model and select the top-N.
  arb          Scan active markets for mispricing (sum of outcomes ≠ 1).
  kelly        One-shot Kelly sizing for a (p_true, p_market) pair.
  agent        Run the news-driven agent loop (fetch RSS → Claude → signals).
  signals      List recorded signals.
  config       Print resolved settings.
"""
from __future__ import annotations

import json
import time

import typer
from rich.console import Console
from rich.table import Table

from .analytics import compute_edge, scan_top_wallets, select_repeatable_edge
from .arb import scan_arbitrage
from .config import settings
from .execution.signal import signal_store
from .polymarket import SubgraphClient
from .sizing import kelly_size


app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)
console = Console()


@app.command()
def wallets(
    days: int = typer.Option(90, help="Lookback window in days."),
    limit: int = typer.Option(50, help="How many wallets to display."),
):
    """Top wallets by realized PnL over the lookback window."""
    console.print(f"[bold]Scanning top wallets — last {days} days[/]")
    rows = scan_top_wallets(days=days, limit=limit)
    table = Table("Rank", "Address", "PnL (USD)", "Volume (USD)", "Positions")
    for i, w in enumerate(rows, 1):
        table.add_row(
            str(i),
            w.address,
            f"{w.pnl_usd:,.0f}",
            f"{w.volume_usd:,.0f}",
            str(w.positions),
        )
    console.print(table)


@app.command()
def traders(
    days: int = typer.Option(90, help="Lookback window in days."),
    pool: int = typer.Option(100, help="Top-PnL pool to score."),
    n: int = typer.Option(7, help="How many traders with repeatable edge to keep."),
    min_trades: int = typer.Option(25, help="Minimum trade count to be considered."),
):
    """Isolate the N traders whose edge is repeatable."""
    console.print(f"[bold]Mining repeatable edge — last {days}d, scoring top {pool} wallets[/]")
    wallets_pool = scan_top_wallets(days=days, limit=pool)
    since = int(time.time()) - days * 86_400

    reports = []
    with SubgraphClient() as sg:
        with console.status("[cyan]Pulling per-trader fills from subgraph…"):
            for w in wallets_pool:
                fills = sg.trader_fills(w.address, since_unix=since)
                rep = compute_edge(w.address, fills)
                reports.append(rep)

    picks = select_repeatable_edge(reports, n=n, min_trades=min_trades)
    table = Table(
        "Rank", "Address", "Trades", "Win%", "Sizing", "Timing", "Risk-Adj", "Composite z", "PnL"
    )
    for i, (rep, z, axes) in enumerate(picks, 1):
        table.add_row(
            str(i),
            rep.address,
            str(rep.n_trades),
            f"{rep.win_rate*100:.1f}",
            f"{rep.sizing_discipline:.3f}",
            f"{rep.entry_timing:+.3f}",
            f"{rep.risk_adjusted_edge:+.3f}",
            f"{z:+.2f}",
            f"{rep.pnl_usd:,.0f}",
        )
    console.print(table)
    if not picks:
        console.print("[yellow]No wallets met the repeatable-edge bar — try lowering --min-trades.[/]")


@app.command()
def arb(
    max_markets: int = typer.Option(500, help="Cap on markets scanned."),
    min_volume: float = typer.Option(1_000, help="Skip markets with 24h volume below this."),
    fee_bps: int = typer.Option(200, help="Round-trip fee assumption in bps."),
):
    """Scan active markets for risk-free arb."""
    console.print(f"[bold]Scanning ≤{max_markets} markets for mispricing…[/]")
    opps = scan_arbitrage(max_markets=max_markets, min_volume_24h=min_volume, fee_bps=fee_bps)
    if not opps:
        console.print("[yellow]No arb found.[/]")
        return
    table = Table("Direction", "Profit/$", "Sum", "Vol24h", "Ends", "Question")
    for o in opps[:25]:
        table.add_row(
            o.direction,
            f"{o.profit_per_dollar*100:.2f}%",
            f"{o.sum_price:.4f}",
            f"{o.volume_24h:,.0f}",
            (o.end_date or "")[:10],
            (o.question or "")[:60],
        )
    console.print(table)


@app.command()
def kelly(
    p_true: float = typer.Argument(..., help="Your subjective probability of YES."),
    p_market: float = typer.Argument(..., help="Market price of YES."),
    bankroll: float = typer.Option(None, help="Override bankroll."),
    multiplier: float = typer.Option(None, help="Override Kelly multiplier."),
):
    """One-shot Kelly sizing."""
    r = kelly_size(p_true, p_market, bankroll=bankroll, kelly_multiplier=multiplier)
    table = Table("Field", "Value")
    table.add_row("edge (bps)", f"{r.edge*10_000:+.0f}")
    table.add_row("full Kelly fraction", f"{r.full_kelly_fraction:.4f}")
    table.add_row("scaled fraction", f"{r.scaled_fraction:.4f}")
    table.add_row("dollar size", f"${r.dollar_size:,.2f}")
    table.add_row("shares", f"{r.shares:,.2f}")
    table.add_row("reasoning", r.reasoning)
    console.print(table)


@app.command()
def agent(
    since_minutes: int = typer.Option(60, help="News window in minutes."),
    max_news: int = typer.Option(20, help="Max news items per run."),
    quiet: bool = typer.Option(False, help="Suppress per-step trace."),
):
    """Run the live news → market → signal agent loop."""
    from .agent import run_agent  # local import — needs anthropic SDK only when invoked

    console.print(f"[bold]Agent run — last {since_minutes}m, ≤{max_news} items[/]")
    result = run_agent(since_seconds=since_minutes * 60, max_news=max_news, verbose=not quiet)
    console.print(json.dumps(result, indent=2))


@app.command()
def signals(tail: int = typer.Option(20, help="Show the last N signals.")):
    """List recorded signals."""
    sigs = signal_store.read()[-tail:]
    if not sigs:
        console.print("[yellow]No signals recorded.[/]")
        return
    table = Table("Created", "Outcome", "Side", "p_true", "p_mkt", "Edge bps", "$", "Question")
    for s in sigs:
        table.add_row(
            time.strftime("%H:%M:%S", time.gmtime(s.created_unix)),
            s.outcome,
            s.side,
            f"{s.p_true:.3f}",
            f"{s.p_market:.3f}",
            f"{s.edge_bps:+.0f}",
            f"{s.dollar_size:,.0f}",
            (s.question or "")[:50],
        )
    console.print(table)


@app.command()
def config():
    """Print resolved settings."""
    redacted = {
        **{k: getattr(settings, k) for k in vars(settings)},
        "anthropic_api_key": "set" if settings.anthropic_api_key else "missing",
    }
    console.print(json.dumps(redacted, indent=2, default=str))


if __name__ == "__main__":
    app()
