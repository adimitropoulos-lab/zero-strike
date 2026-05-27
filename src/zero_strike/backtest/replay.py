"""Backtest: replay historical signals against their resolutions.

This isn't a forward-looking simulator (we don't synthesize signals the agent
didn't actually fire) — it's a *what-if-we'd-sized-differently* replay over the
real signal stream the agent has produced. Given the resolutions are ground
truth, we can ask:

  - Would full Kelly have done better than quarter Kelly?
  - Does a 300 bps minimum-edge filter reject more losers than winners?
  - Does the cluster cap actually prevent drawdowns?

The replay re-sizes each signal under the supplied parameters, then realizes
PnL against the same final outcome the live signal got. Sharpe and max
drawdown are computed on the ordered PnL stream (by signal creation time).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..calibration.resolution import signal_key
from ..calibration.store import resolution_store
from ..execution.signal import signal_store
from ..sizing.kelly import kelly_size


@dataclass
class BacktestResult:
    n_signals: int
    n_resolved: int
    total_pnl_usd: float
    win_rate: float
    sharpe: float | None
    max_drawdown_usd: float
    avg_dollar_size: float
    avg_edge_bps: float
    # The parameter set replayed.
    bankroll: float
    kelly_fraction: float
    max_position_pct: float
    min_edge_bps: int

    def as_row(self) -> dict:
        return {
            "n_resolved": self.n_resolved,
            "pnl": round(self.total_pnl_usd, 2),
            "win_rate": round(self.win_rate, 3),
            "sharpe": (round(self.sharpe, 2) if self.sharpe is not None else None),
            "max_dd": round(self.max_drawdown_usd, 2),
            "avg_size": round(self.avg_dollar_size, 2),
            "kelly_frac": self.kelly_fraction,
            "min_edge_bps": self.min_edge_bps,
            "max_pos_pct": self.max_position_pct,
        }


def _sharpe(pnl_stream: list[float]) -> float | None:
    if len(pnl_stream) < 2:
        return None
    mu = sum(pnl_stream) / len(pnl_stream)
    var = sum((x - mu) ** 2 for x in pnl_stream) / (len(pnl_stream) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return mu / sd * math.sqrt(len(pnl_stream))


def _max_drawdown(pnl_stream: list[float]) -> float:
    """Largest peak-to-trough drop on the equity curve."""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnl_stream:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    return max_dd


def replay(
    *,
    bankroll: float = 10_000,
    kelly_fraction: float = 0.25,
    max_position_pct: float = 0.05,
    min_edge_bps: int = 200,
) -> BacktestResult:
    sigs = signal_store.read()
    res = resolution_store.latest_by_signal()

    sigs_sorted = sorted(sigs, key=lambda s: s.created_unix)
    pnl_stream: list[float] = []
    sizes: list[float] = []
    edges: list[float] = []
    wins = 0
    resolved = 0

    for s in sigs_sorted:
        r = res.get(signal_key(s))
        if r is None or r.outcome_indicator is None:
            continue
        # Re-size from scratch using replay parameters.
        k = kelly_size(
            s.p_true,
            s.p_market,
            bankroll=bankroll,
            kelly_multiplier=kelly_fraction,
            max_position_pct=max_position_pct,
            min_edge_bps=min_edge_bps,
        )
        if k.dollar_size <= 0:
            continue
        shares = k.shares
        # Same direction the live signal took; the replay only changes size, not direction.
        if s.side.upper() == "BUY":
            realized = shares * r.outcome_indicator - k.dollar_size
        else:
            realized = k.dollar_size - shares * r.outcome_indicator
        pnl_stream.append(realized)
        sizes.append(k.dollar_size)
        edges.append(s.edge_bps)
        resolved += 1
        if realized > 0:
            wins += 1

    return BacktestResult(
        n_signals=len(sigs),
        n_resolved=resolved,
        total_pnl_usd=sum(pnl_stream),
        win_rate=(wins / resolved) if resolved else 0.0,
        sharpe=_sharpe(pnl_stream),
        max_drawdown_usd=_max_drawdown(pnl_stream),
        avg_dollar_size=(sum(sizes) / len(sizes)) if sizes else 0.0,
        avg_edge_bps=(sum(edges) / len(edges)) if edges else 0.0,
        bankroll=bankroll,
        kelly_fraction=kelly_fraction,
        max_position_pct=max_position_pct,
        min_edge_bps=min_edge_bps,
    )
