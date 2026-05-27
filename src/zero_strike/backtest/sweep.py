"""Parameter sweep — replay across a grid and find the best-Sharpe config."""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from .replay import BacktestResult, replay


@dataclass
class SweepResult:
    results: list[BacktestResult]

    def by_sharpe(self) -> list[BacktestResult]:
        return sorted(
            self.results,
            key=lambda r: (r.sharpe is None, -(r.sharpe or 0)),
        )

    def by_pnl(self) -> list[BacktestResult]:
        return sorted(self.results, key=lambda r: r.total_pnl_usd, reverse=True)


def sweep_parameters(
    *,
    bankroll: float = 10_000,
    kelly_fractions: list[float] | None = None,
    max_position_pcts: list[float] | None = None,
    min_edge_bps_list: list[int] | None = None,
) -> SweepResult:
    kelly_fractions = kelly_fractions or [0.1, 0.25, 0.5, 1.0]
    max_position_pcts = max_position_pcts or [0.02, 0.05, 0.10]
    min_edge_bps_list = min_edge_bps_list or [100, 200, 300, 500]

    results: list[BacktestResult] = []
    for kf, mp, me in itertools.product(kelly_fractions, max_position_pcts, min_edge_bps_list):
        results.append(
            replay(
                bankroll=bankroll,
                kelly_fraction=kf,
                max_position_pct=mp,
                min_edge_bps=me,
            )
        )
    return SweepResult(results=results)
