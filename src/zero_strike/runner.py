"""Orchestrator — runs every Zero-Strike loop on its right cadence in one process.

Cadences are tuned to the half-life of each signal type:

  arb       60s    — books move fast; mispricing closes in seconds
  agent     5min   — news has minutes of dislocation before the market chews it
  traders   24h    — the repeatable-edge cohort shifts slowly; refresh daily

Each loop catches its own exceptions so one bad cycle doesn't kill the others.
Signals are appended to ~/.zero-strike/signals.jsonl and, if SIGNAL_WEBHOOK_URL
is set, POSTed as JSON to that URL (Discord/Slack/etc).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict
from typing import Awaitable, Callable

import httpx
from rich.console import Console

from .agent import run_agent
from .analytics import (
    CohortMember,
    CohortSnapshot,
    cohort_store,
    compute_edge,
    scan_top_wallets,
    select_repeatable_edge,
)
from .arb import scan_arbitrage
from .calibration.daemon import sweep_once
from .execution.signal import Signal, signal_store
from .observability import start_metrics_server
from .polymarket import SubgraphClient


console = Console()


class Runner:
    def __init__(
        self,
        *,
        arb_interval: int = 60,
        agent_interval: int = 300,
        traders_interval: int = 86_400,
        resolution_interval: int = 3600,
        agent_news_window: int = 600,
        webhook_url: str | None = None,
        metrics_port: int | None = 9090,
    ):
        self.arb_interval = arb_interval
        self.agent_interval = agent_interval
        self.traders_interval = traders_interval
        self.resolution_interval = resolution_interval
        self.agent_news_window = agent_news_window
        self.webhook_url = webhook_url or os.getenv("SIGNAL_WEBHOOK_URL")
        self.metrics_port = metrics_port
        self._last_signal_count = len(signal_store.read())
        self._stop = asyncio.Event()
        self._cohort: list[str] = []

    def stop(self) -> None:
        self._stop.set()

    async def _every(self, name: str, interval: int, fn: Callable[[], Awaitable[None]]) -> None:
        # Stagger initial fires so they don't all hit the network at once.
        await asyncio.sleep({"arb": 1, "agent": 3, "traders": 5, "resolution": 7}.get(name, 0))
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                await fn()
            except Exception as e:
                console.print(f"[red][{name}] error: {type(e).__name__}: {e}[/]")
            await self._flush_new_signals()
            elapsed = time.monotonic() - t0
            wait = max(1.0, interval - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass

    async def _arb_cycle(self) -> None:
        opps = await asyncio.to_thread(scan_arbitrage, max_markets=300, min_volume_24h=1_000)
        if not opps:
            console.print(f"[dim][{_ts()}] [arb] clean[/]")
            return
        top = opps[0]
        console.print(
            f"[green][{_ts()}] [arb] {len(opps)} opps — best {top.profit_per_dollar*100:.2f}% "
            f"{top.direction} on '{top.question[:60]}'[/]"
        )
        # An arb is itself a signal worth recording.
        for o in opps[:5]:
            sig = Signal(
                market_id=o.market_id,
                question=o.question,
                outcome=",".join(o.outcomes),
                side="BUY" if o.direction == "buy_all" else "SELL",
                p_true=1.0 if o.direction == "buy_all" else 0.0,
                p_market=o.sum_price,
                shares=0.0,
                dollar_size=0.0,
                edge_bps=round(o.profit_per_dollar * 10_000, 1),
                rationale=f"arb: sum={o.sum_price:.4f}, profit/$={o.profit_per_dollar:.4f}",
                news_refs=[],
                expires_in_minutes=5,
            )
            signal_store.append(sig)

    async def _agent_cycle(self) -> None:
        console.print(f"[cyan][{_ts()}] [agent] starting run on last {self.agent_news_window // 60}m of news[/]")
        result = await asyncio.to_thread(
            run_agent, since_seconds=self.agent_news_window, max_news=20, verbose=False
        )
        console.print(
            f"[cyan][{_ts()}] [agent] stop={result.get('stop')} steps={result.get('steps')} "
            f"tools={result.get('tool_calls')} news={result.get('news_items')}[/]"
        )

    async def _traders_cycle(self) -> None:
        console.print(f"[magenta][{_ts()}] [traders] refreshing 7-cohort over 90d…[/]")
        wallets = await asyncio.to_thread(scan_top_wallets, days=90, limit=100)
        since = int(time.time()) - 90 * 86_400
        reports = []
        with SubgraphClient() as sg:
            for w in wallets:
                fills = await asyncio.to_thread(sg.trader_fills, w.address, since_unix=since)
                reports.append(compute_edge(w.address, fills))
        picks = select_repeatable_edge(reports, n=7, min_trades=25)
        self._cohort = [r.address for r, _, _ in picks]
        if picks:
            snap = CohortSnapshot(
                selected_unix=int(time.time()),
                lookback_days=90,
                members=[
                    CohortMember(
                        address=rep.address, composite_z=z, win_rate=rep.win_rate,
                        n_trades=rep.n_trades, pnl_usd=rep.pnl_usd,
                    )
                    for rep, z, _ in picks
                ],
            )
            cohort_store.save(snap)
        console.print(
            f"[magenta][{_ts()}] [traders] cohort: "
            + (", ".join(a[:8] + "…" for a in self._cohort) or "(none met bar)")
            + "[/]"
        )

    async def _resolution_cycle(self) -> None:
        result = await asyncio.to_thread(sweep_once)
        if result.scanned == 0 and result.newly_resolved == 0:
            console.print(f"[dim][{_ts()}] [resolve] nothing to settle[/]")
            return
        console.print(
            f"[blue][{_ts()}] [resolve] scanned={result.scanned} "
            f"resolved={result.newly_resolved} still_open={result.still_open} "
            f"errors={result.errors} cum_pnl=${result.cumulative_pnl:,.0f}[/]"
        )

    async def _flush_new_signals(self) -> None:
        all_sigs = signal_store.read()
        new = all_sigs[self._last_signal_count :]
        self._last_signal_count = len(all_sigs)
        if not new:
            return
        console.print(f"[bold yellow][{_ts()}] [signals] {len(new)} new[/]")
        if self.webhook_url:
            async with httpx.AsyncClient(timeout=10) as client:
                for sig in new:
                    try:
                        await client.post(self.webhook_url, json=asdict(sig))
                    except Exception as e:
                        console.print(f"[red][webhook] {type(e).__name__}: {e}[/]")

    async def run(self) -> None:
        if self.metrics_port:
            try:
                start_metrics_server(host="0.0.0.0", port=self.metrics_port)
                console.print(f"[bold]metrics → http://0.0.0.0:{self.metrics_port}/metrics[/]")
            except OSError as e:
                console.print(f"[yellow]metrics server skipped: {e}[/]")
        console.print(
            f"[bold]Zero-Strike runner up — arb/{self.arb_interval}s, "
            f"agent/{self.agent_interval}s, traders/{self.traders_interval}s, "
            f"resolve/{self.resolution_interval}s, "
            f"webhook={'on' if self.webhook_url else 'off'}[/]"
        )
        await asyncio.gather(
            self._every("arb", self.arb_interval, self._arb_cycle),
            self._every("agent", self.agent_interval, self._agent_cycle),
            self._every("traders", self.traders_interval, self._traders_cycle),
            self._every("resolution", self.resolution_interval, self._resolution_cycle),
        )


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def run_forever(**kwargs) -> None:
    runner = Runner(**kwargs)
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        console.print("\n[bold]Zero-Strike stopped.[/]")
