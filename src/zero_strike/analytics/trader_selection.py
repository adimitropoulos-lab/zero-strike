"""Select N traders whose edge is *repeatable* — not concentration luck.

The filter:
  1. Minimum trade count (`min_trades`) so single-bet whales fall out.
  2. Cross-sectional z-score across the four edge axes; trader must clear
     a positive threshold on at least three of four.
  3. Rank by composite z-sum, take top N.
"""
from __future__ import annotations

import statistics
from typing import Iterable

from .edge_metrics import EdgeReport


def _zscores(values: list[float]) -> list[float]:
    if not values:
        return []
    mu = statistics.mean(values)
    sd = statistics.pstdev(values) or 1.0
    return [(v - mu) / sd for v in values]


def select_repeatable_edge(
    reports: Iterable[EdgeReport],
    *,
    n: int = 7,
    min_trades: int = 25,
    min_axes_above_zero: int = 3,
) -> list[tuple[EdgeReport, float, dict[str, float]]]:
    """Return (report, composite_z, per_axis_z) for the top `n` traders."""
    pool = [r for r in reports if r.n_trades >= min_trades]
    if not pool:
        return []

    z_win = _zscores([r.win_rate for r in pool])
    z_size = _zscores([r.sizing_discipline for r in pool])
    z_time = _zscores([r.entry_timing for r in pool])
    z_risk = _zscores([r.risk_adjusted_edge for r in pool])

    scored: list[tuple[EdgeReport, float, dict[str, float]]] = []
    for i, r in enumerate(pool):
        axes = {
            "win_rate": z_win[i],
            "sizing_discipline": z_size[i],
            "entry_timing": z_time[i],
            "risk_adjusted_edge": z_risk[i],
        }
        if sum(1 for v in axes.values() if v > 0) < min_axes_above_zero:
            continue
        composite = sum(axes.values())
        scored.append((r, composite, axes))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n]
