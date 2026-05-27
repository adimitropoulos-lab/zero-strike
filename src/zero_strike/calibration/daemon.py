"""Resolution daemon — sweeps unresolved signals and settles closed ones."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ..execution.signal import signal_store
from ..polymarket import GammaClient
from .resolution import resolve_signal, signal_key
from .store import resolution_store


@dataclass
class SweepResult:
    scanned: int
    newly_resolved: int
    still_open: int
    errors: int
    cumulative_pnl: float


def sweep_once() -> SweepResult:
    """One pass: resolve any signal whose market is closed and isn't already settled."""
    signals = signal_store.read()
    settled = {
        k: r
        for k, r in resolution_store.latest_by_signal().items()
        if r.market_closed and r.outcome_indicator is not None
    }

    scanned = 0
    newly = 0
    still_open = 0
    errors = 0
    pnl = sum(r.realized_pnl_usd for r in settled.values())

    seen_markets: dict[str, dict | None] = {}
    with GammaClient() as gamma:
        for s in signals:
            key = signal_key(s)
            if key in settled:
                continue
            # Arb-set signals (comma-joined outcome labels) can't be resolved per-leg.
            if "," in s.outcome:
                continue
            scanned += 1
            r = resolve_signal(s, gamma=gamma)
            resolution_store.append(r)
            if r.notes.startswith("fetch_error"):
                errors += 1
            elif r.market_closed and r.outcome_indicator is not None:
                newly += 1
                pnl += r.realized_pnl_usd
            else:
                still_open += 1

    return SweepResult(scanned=scanned, newly_resolved=newly, still_open=still_open,
                       errors=errors, cumulative_pnl=pnl)


async def daemon_loop(*, interval_seconds: int = 3600, stop_event: asyncio.Event | None = None) -> None:
    """Sweep on interval until stop_event is set (or forever)."""
    while True:
        await asyncio.to_thread(sweep_once)
        if stop_event is not None:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                return
            except asyncio.TimeoutError:
                continue
        await asyncio.sleep(interval_seconds)
