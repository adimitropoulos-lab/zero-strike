"""Format calibration stats for injection into the agent's system prompt.

We only inject when there's enough resolved-signal data to be meaningful
(default ≥10 resolutions). Below that, small-N noise would push the agent
to overcorrect on a handful of outcomes.
"""
from __future__ import annotations

from .metrics import CalibrationStats


def format_calibration_for_prompt(stats: CalibrationStats, *, min_resolved: int = 10) -> str:
    if stats.n_resolved < min_resolved:
        return ""

    lines = [
        "",
        "## Your historical calibration (resolved signals only — read this before sizing)",
        f"- resolved: {stats.n_resolved} / {stats.n_signals} signals",
        f"- Brier: {stats.brier:.4f}   (0.25 = uninformed, lower = better)",
        f"- hit rate: {stats.hit_rate*100:.1f}%",
        f"- realized PnL: ${stats.realized_pnl_usd:,.0f}",
        "",
        "## Reliability buckets — predicted p_true vs actual outcome rate",
    ]
    for bk in stats.buckets:
        if bk.n == 0:
            continue
        drift = bk.actual_rate - bk.predicted_mean
        flag = ""
        if abs(drift) > 0.10 and bk.n >= 5:
            flag = "  ← OVERCONFIDENT" if drift < 0 else "  ← UNDERCONFIDENT"
        lines.append(
            f"- p∈{bk.label()}  n={bk.n:3d}  predicted={bk.predicted_mean:.2f}  "
            f"actual={bk.actual_rate:.2f}  drift={drift:+.2f}{flag}"
        )

    lines.append("")
    lines.append(
        "Calibration guidance: if buckets show OVERCONFIDENT drift, shrink your "
        "p_true estimates toward 0.5 in that range. If UNDERCONFIDENT, you may "
        "be leaving edge on the table — but verify with more evidence before "
        "pushing estimates further from the market."
    )
    return "\n".join(lines)
