"""Zero-Strike CLI — one entry point for every capability.

Commands:
  wallets      Pull top wallets over a window and print P&L leaderboard.
  traders      Score wallets on the 4-axis edge model and select the top-N.
  arb          Scan active markets for mispricing (sum of outcomes ≠ 1).
  kelly        One-shot Kelly sizing for a (p_true, p_market) pair.
  agent        Run the news-driven agent loop (fetch RSS → Claude → signals).
  resolve      Settle any signals whose markets have closed.
  calibration  Show Brier / hit-rate / PnL / reliability buckets.
  backtest     Replay resolved signals under different sizing parameters.
  portfolio    Show open exposure by event (cluster view).
  budget       Show Anthropic spend (today + last 14d) vs daily cap.
  cohort       Show persisted 7-cohort and (optionally) recent fills.
  metrics      Render Prometheus metrics once to stdout.
  execute      Submit recent signals to Polymarket (dry-run by default).
  halt         Halt live execution.
  unhalt       Lift the halt flag.
  live-log     Show recent live-execution records.
  run          One-click: run arb + agent + traders + resolution loops forever.
  signals      List recorded signals.
  config       Print resolved settings.
"""
from __future__ import annotations

import json
import time

import typer
from rich.console import Console
from rich.table import Table

from .agent.budget import daily_cap_usd, spend_store
from .analytics import (
    CohortMember,
    CohortSnapshot,
    cohort_store,
    compute_edge,
    poll_new_activity,
    scan_top_wallets,
    select_repeatable_edge,
)
from .arb import scan_arbitrage
from .backtest import sweep_parameters
from .backtest.replay import replay
from .calibration import compute_stats, resolution_store
from .calibration.daemon import sweep_once
from .config import settings
from .execution import (
    execute_recent_signals,
    halt as halt_execution,
    is_halted,
    read_records as read_live_records,
    unhalt as unhalt_execution,
)
from .execution.signal import signal_store
from .observability import collect_metrics, render_prometheus
from .polymarket import SubgraphClient
from .sizing import kelly_size, open_exposure_by_event


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
    persist: bool = typer.Option(True, help="Save the selected cohort so the agent can mirror it."),
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
        return
    if persist:
        snap = CohortSnapshot(
            selected_unix=int(time.time()),
            lookback_days=days,
            members=[
                CohortMember(
                    address=rep.address,
                    composite_z=z,
                    win_rate=rep.win_rate,
                    n_trades=rep.n_trades,
                    pnl_usd=rep.pnl_usd,
                )
                for rep, z, _ in picks
            ],
        )
        cohort_store.save(snap)
        console.print(f"[green]Cohort saved → {cohort_store.path}[/]")


@app.command()
def cohort(
    activity: bool = typer.Option(False, help="Also show recent fills since last poll."),
    lookback_seconds: int = typer.Option(3600, help="Activity lookback for --activity."),
):
    """Show the current 7-cohort (and optionally recent activity)."""
    snap = cohort_store.load()
    if snap is None:
        console.print("[yellow]No cohort persisted yet — run `zero-strike traders` first.[/]")
        return
    t = Table("Rank", "Address", "Composite z", "Win%", "Trades", "PnL")
    for i, m in enumerate(snap.members, 1):
        t.add_row(
            str(i), m.address, f"{m.composite_z:+.2f}",
            f"{m.win_rate*100:.1f}", str(m.n_trades), f"${m.pnl_usd:,.0f}",
        )
    console.print(f"[bold]Cohort selected {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(snap.selected_unix))} "
                  f"(lookback {snap.lookback_days}d)[/]")
    console.print(t)
    if activity:
        fills = poll_new_activity(lookback_seconds=lookback_seconds)
        if not fills:
            console.print("[dim]No new activity since last poll.[/]")
            return
        a = Table("Time", "Address", "Side", "Token", "USDC", "Avg px")
        for f in fills[:30]:
            a.add_row(
                time.strftime("%H:%M:%S", time.gmtime(f.timestamp)),
                f.address[:10],
                f.side,
                f.token_id[:16],
                f"${f.usdc_amount:,.0f}",
                f"{f.avg_price:.4f}",
            )
        console.print(a)


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
def run(
    arb_interval: int = typer.Option(60, help="Arb scan cadence in seconds."),
    agent_interval: int = typer.Option(300, help="News→agent cadence in seconds."),
    traders_interval: int = typer.Option(86_400, help="Top-7 cohort refresh cadence in seconds."),
    resolution_interval: int = typer.Option(3600, help="Resolution-sweep cadence in seconds."),
    agent_news_window: int = typer.Option(600, help="How far back the agent reads news, in seconds."),
    webhook: str = typer.Option(None, help="Optional URL to POST each new signal as JSON."),
    metrics_port: int = typer.Option(9090, help="Port for Prometheus /metrics endpoint (0 to disable)."),
):
    """One-click: run arb + agent + traders + resolution loops forever (Ctrl-C to stop)."""
    from .runner import run_forever

    run_forever(
        arb_interval=arb_interval,
        agent_interval=agent_interval,
        traders_interval=traders_interval,
        resolution_interval=resolution_interval,
        agent_news_window=agent_news_window,
        webhook_url=webhook,
        metrics_port=metrics_port if metrics_port > 0 else None,
    )


@app.command()
def resolve():
    """One-shot: settle any signals whose markets have closed."""
    result = sweep_once()
    table = Table("Field", "Value")
    table.add_row("scanned (open before sweep)", str(result.scanned))
    table.add_row("newly resolved", str(result.newly_resolved))
    table.add_row("still open", str(result.still_open))
    table.add_row("fetch errors", str(result.errors))
    table.add_row("cumulative realized PnL", f"${result.cumulative_pnl:,.2f}")
    console.print(table)


@app.command()
def calibration():
    """Brier / hit-rate / realized PnL / reliability buckets across all resolved signals."""
    sigs = signal_store.read()
    res = resolution_store.latest_by_signal()
    stats = compute_stats(sigs, res)

    summary = Table("Metric", "Value")
    summary.add_row("signals", str(stats.n_signals))
    summary.add_row("resolved", str(stats.n_resolved))
    summary.add_row("Brier", f"{stats.brier:.4f}" if stats.brier is not None else "—")
    summary.add_row("hit rate", f"{stats.hit_rate*100:.1f}%" if stats.hit_rate is not None else "—")
    summary.add_row("realized PnL", f"${stats.realized_pnl_usd:,.2f}")
    summary.add_row("avg edge (bps)", f"{stats.avg_edge_bps:+.0f}")
    console.print(summary)

    if stats.n_resolved == 0:
        console.print("[yellow]No resolved signals yet — let it run.[/]")
        return

    buckets = Table("p_true range", "n", "predicted", "actual", "drift")
    for bk in stats.buckets:
        if bk.n == 0:
            continue
        drift = bk.actual_rate - bk.predicted_mean
        buckets.add_row(
            bk.label(),
            str(bk.n),
            f"{bk.predicted_mean:.2f}",
            f"{bk.actual_rate:.2f}",
            f"{drift:+.2f}",
        )
    console.print(buckets)


@app.command()
def backtest(
    sweep: bool = typer.Option(False, help="Sweep parameter grid (default: single replay with live settings)."),
    kelly: float = typer.Option(None, help="Kelly multiplier for the single replay."),
    max_position: float = typer.Option(None, help="Max position % of bankroll for the single replay."),
    min_edge: int = typer.Option(None, help="Min edge in bps for the single replay."),
):
    """Replay resolved signals under different sizing parameters."""
    if not sweep:
        r = replay(
            bankroll=settings.bankroll,
            kelly_fraction=kelly if kelly is not None else settings.kelly_fraction,
            max_position_pct=max_position if max_position is not None else settings.max_position_pct,
            min_edge_bps=min_edge if min_edge is not None else settings.min_edge_bps,
        )
        t = Table("Metric", "Value")
        t.add_row("signals total", str(r.n_signals))
        t.add_row("signals resolved+replayed", str(r.n_resolved))
        t.add_row("total PnL", f"${r.total_pnl_usd:,.2f}")
        t.add_row("win rate", f"{r.win_rate*100:.1f}%")
        t.add_row("Sharpe", f"{r.sharpe:.2f}" if r.sharpe is not None else "—")
        t.add_row("max drawdown", f"${r.max_drawdown_usd:,.2f}")
        t.add_row("avg size", f"${r.avg_dollar_size:,.2f}")
        t.add_row("avg edge (bps)", f"{r.avg_edge_bps:+.0f}")
        t.add_row("kelly multiplier", f"{r.kelly_fraction}")
        t.add_row("max position pct", f"{r.max_position_pct}")
        t.add_row("min edge bps", f"{r.min_edge_bps}")
        console.print(t)
        return

    sr = sweep_parameters(bankroll=settings.bankroll)
    top = sr.by_sharpe()[:10]
    table = Table("Rank", "Kelly", "MaxPos%", "MinEdge", "Resolved", "PnL", "Sharpe", "MaxDD", "WinRate")
    for i, r in enumerate(top, 1):
        table.add_row(
            str(i),
            f"{r.kelly_fraction}",
            f"{r.max_position_pct}",
            str(r.min_edge_bps),
            str(r.n_resolved),
            f"${r.total_pnl_usd:,.0f}",
            f"{r.sharpe:.2f}" if r.sharpe is not None else "—",
            f"${r.max_drawdown_usd:,.0f}",
            f"{r.win_rate*100:.1f}%",
        )
    console.print(table)
    if not top or top[0].n_resolved == 0:
        console.print("[yellow]No resolved signals to replay — let the agent run.[/]")


@app.command()
def portfolio():
    """Open exposure by event cluster — what's already at risk."""
    by_event = open_exposure_by_event()
    if not by_event:
        console.print("[yellow]No open exposure by event.[/]")
        return
    table = Table("Event", "Open Signals", "Dollar Exposure", "Markets")
    for event_id, items in sorted(by_event.items(), key=lambda x: -sum(i["dollar_size"] for i in x[1])):
        total = sum(i["dollar_size"] for i in items)
        markets = ", ".join(sorted({i["market_id"][:10] for i in items}))[:60]
        table.add_row(event_id[:16], str(len(items)), f"${total:,.2f}", markets)
    console.print(table)


@app.command()
def budget(days: int = typer.Option(14, help="Look back this many days.")):
    """Show Anthropic spend vs daily cap."""
    cap = daily_cap_usd()
    today_spend = spend_store.spend_today()
    pct = (today_spend / cap * 100) if cap > 0 else 0
    summary = Table("Metric", "Value")
    summary.add_row("daily cap (USD)", f"${cap:.2f}")
    summary.add_row("spend today (USD)", f"${today_spend:.4f}")
    summary.add_row("% of cap", f"{pct:.1f}%")
    summary.add_row("remaining today", f"${max(0, cap - today_spend):.4f}")
    console.print(summary)

    by_day = spend_store.spend_by_day()
    if by_day:
        recent = sorted(by_day.items(), reverse=True)[:days]
        history = Table("Date (UTC)", "Spend (USD)")
        for d, v in recent:
            history.add_row(d, f"${v:.4f}")
        console.print(history)


@app.command()
def metrics():
    """Render Prometheus metrics once to stdout (same content as /metrics endpoint)."""
    console.print(render_prometheus(collect_metrics()))


@app.command()
def execute(
    live: bool = typer.Option(False, "--live", help="Actually submit orders (default: dry-run preview)."),
    signal_max_age: int = typer.Option(1800, help="Skip signals older than N seconds."),
):
    """Submit recent signals to Polymarket. --live required for real submission."""
    halted, hreason = is_halted()
    if halted:
        console.print(f"[red]HALTED — {hreason}. Run `zero-strike unhalt` after investigating.[/]")
        if live:
            raise typer.Exit(code=1)
    if not live:
        console.print("[yellow]DRY-RUN: showing what would be submitted. Pass --live to actually send.[/]")
    results = execute_recent_signals(dry_run=not live, signal_max_age_s=signal_max_age)
    if not results:
        console.print("[dim]No recent signals to execute.[/]")
        return
    t = Table("Submitted", "Reason", "Order ID")
    for r in results:
        t.add_row(str(r.submitted), r.reason[:80], r.order_id or "")
    console.print(t)


@app.command()
def halt(reason: str = typer.Argument(..., help="Why are you halting?")):
    """Halt all live execution. Affects the --live flag only."""
    halt_execution(reason)
    console.print(f"[red]HALTED: {reason}[/]")


@app.command()
def unhalt():
    """Lift the halt flag. Investigate root cause first."""
    unhalt_execution()
    console.print("[green]Halt lifted.[/]")


@app.command(name="live-log")
def live_log(tail: int = typer.Option(20, help="Last N records.")):
    """Show recent live-execution records (submitted + rejected)."""
    recs = read_live_records()[-tail:]
    if not recs:
        console.print("[dim]No live-execution records.[/]")
        return
    t = Table("Time", "Subm", "Side", "Market", "Outcome", "$", "Price", "Reason")
    for r in recs:
        t.add_row(
            time.strftime("%H:%M:%S", time.gmtime(r.ts_unix)),
            "Y" if r.submitted else "n",
            r.side, r.market_id[:10], r.outcome[:12],
            f"${r.dollar_size:.0f}", f"{r.price:.3f}",
            r.reason[:30],
        )
    console.print(t)


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
