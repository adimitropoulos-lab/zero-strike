"""Metric collectors — read from existing stores, no extra accounting layer.

We avoid Prometheus client-lib deps; the text format is trivial enough to render
by hand and the data fits in memory.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

from ..agent.budget import daily_cap_usd, spend_store
from ..calibration import compute_stats, resolution_store
from ..execution.signal import signal_store
from ..sizing import open_exposure_by_event


@dataclass
class Metric:
    name: str
    type: str   # counter | gauge
    help: str
    value: float
    labels: dict[str, str] | None = None


def collect_metrics() -> list[Metric]:
    now = int(time.time())
    sigs = signal_store.read()
    resolutions = resolution_store.latest_by_signal()
    stats = compute_stats(sigs, resolutions)
    exposure = open_exposure_by_event()
    total_open_dollar = sum(sum(i["dollar_size"] for i in lst) for lst in exposure.values())
    open_count = sum(len(lst) for lst in exposure.values())

    cutoff_1h = now - 3600
    cutoff_24h = now - 86_400
    signals_1h = sum(1 for s in sigs if s.created_unix >= cutoff_1h)
    signals_24h = sum(1 for s in sigs if s.created_unix >= cutoff_24h)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    spend_today = sum(r.cost_usd for r in spend_store.read() if r.date == today)
    cap = daily_cap_usd()

    metrics: list[Metric] = [
        Metric("zs_signals_total", "counter", "Total signals ever emitted", float(len(sigs))),
        Metric("zs_signals_last_1h", "gauge", "Signals in the last hour", float(signals_1h)),
        Metric("zs_signals_last_24h", "gauge", "Signals in the last 24h", float(signals_24h)),
        Metric("zs_signals_resolved_total", "counter", "Resolved signals", float(stats.n_resolved)),
        Metric("zs_realized_pnl_usd", "gauge", "Cumulative realized PnL from resolved signals", stats.realized_pnl_usd),
        Metric("zs_calibration_brier", "gauge", "Brier score across resolved signals (lower better)", stats.brier or 0.0),
        Metric("zs_hit_rate", "gauge", "Fraction of resolved signals with positive PnL", stats.hit_rate or 0.0),
        Metric("zs_open_positions", "gauge", "Open positions (unresolved signals with event_id)", float(open_count)),
        Metric("zs_open_exposure_usd", "gauge", "Total dollar exposure across open positions", total_open_dollar),
        Metric("zs_event_clusters_open", "gauge", "Distinct event clusters with open exposure", float(len(exposure))),
        Metric("zs_spend_today_usd", "gauge", "Anthropic spend today (UTC)", spend_today),
        Metric("zs_spend_cap_usd", "gauge", "Daily Anthropic spend cap", cap),
        Metric("zs_spend_pct_of_cap", "gauge", "Today's spend as fraction of cap", (spend_today / cap) if cap > 0 else 0),
    ]

    # Per-event exposure as a labeled gauge.
    for event_id, items in exposure.items():
        metrics.append(
            Metric(
                "zs_cluster_exposure_usd",
                "gauge",
                "Open dollar exposure per event cluster",
                sum(i["dollar_size"] for i in items),
                labels={"event_id": event_id},
            )
        )
    return metrics


def render_prometheus(metrics: list[Metric]) -> str:
    """Emit standard Prometheus exposition format."""
    seen_headers: set[str] = set()
    lines: list[str] = []
    for m in metrics:
        if m.name not in seen_headers:
            lines.append(f"# HELP {m.name} {m.help}")
            lines.append(f"# TYPE {m.name} {m.type}")
            seen_headers.add(m.name)
        if m.labels:
            label_str = ",".join(f'{k}="{_escape(v)}"' for k, v in m.labels.items())
            lines.append(f'{m.name}{{{label_str}}} {m.value}')
        else:
            lines.append(f"{m.name} {m.value}")
    lines.append("")
    return "\n".join(lines)


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
