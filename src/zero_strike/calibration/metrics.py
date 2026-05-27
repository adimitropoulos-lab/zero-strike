"""Calibration metrics — Brier, hit-rate, reliability buckets, realized PnL.

Brier score is the mean squared error between predicted probability and the
realized 0/1 outcome. Lower is better; 0 = perfect, 0.25 = always saying 0.5,
0.5 = always wrong. We also bucket signals by predicted probability to detect
systematic over-/under-confidence (calibration drift).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from ..execution.signal import Signal
from .resolution import signal_key
from .store import Resolution


@dataclass
class Bucket:
    lo: float
    hi: float
    n: int = 0
    predicted_mean: float = 0.0
    actual_rate: float = 0.0

    def label(self) -> str:
        return f"[{self.lo:.2f},{self.hi:.2f})"


@dataclass
class CalibrationStats:
    n_signals: int
    n_resolved: int
    brier: float | None
    hit_rate: float | None
    realized_pnl_usd: float
    avg_edge_bps: float
    buckets: list[Bucket] = field(default_factory=list)


def _bucket(p: float, edges: list[float]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= p < edges[i + 1]:
            return i
    return len(edges) - 2


def compute_stats(
    signals: list[Signal],
    resolutions: dict[str, Resolution],
    *,
    bucket_edges: list[float] | None = None,
) -> CalibrationStats:
    edges = bucket_edges or [0.0, 0.2, 0.4, 0.6, 0.8, 1.0001]
    buckets = [Bucket(lo=edges[i], hi=edges[i + 1]) for i in range(len(edges) - 1)]
    bucket_preds: list[list[float]] = [[] for _ in buckets]
    bucket_actuals: list[list[float]] = [[] for _ in buckets]

    resolved_briers: list[float] = []
    resolved_hits: list[bool] = []
    realized = 0.0

    for s in signals:
        r = resolutions.get(signal_key(s))
        if r is None or r.brier is None or r.outcome_indicator is None:
            continue
        resolved_briers.append(r.brier)
        resolved_hits.append(bool(r.hit))
        realized += r.realized_pnl_usd
        b = _bucket(s.p_true, edges)
        bucket_preds[b].append(s.p_true)
        bucket_actuals[b].append(r.outcome_indicator)

    for i, bk in enumerate(buckets):
        if bucket_preds[i]:
            bk.n = len(bucket_preds[i])
            bk.predicted_mean = mean(bucket_preds[i])
            bk.actual_rate = mean(bucket_actuals[i])

    return CalibrationStats(
        n_signals=len(signals),
        n_resolved=len(resolved_briers),
        brier=mean(resolved_briers) if resolved_briers else None,
        hit_rate=(sum(resolved_hits) / len(resolved_hits)) if resolved_hits else None,
        realized_pnl_usd=realized,
        avg_edge_bps=(mean(s.edge_bps for s in signals) if signals else 0.0),
        buckets=buckets,
    )
