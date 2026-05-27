"""Format cohort fills as agent input — parallel to news_feed."""
from __future__ import annotations

from dataclasses import dataclass

from ..analytics import poll_new_activity


@dataclass
class CohortInput:
    text: str
    source: str = "cohort"


def fetch_cohort_input(*, lookback_seconds: int = 3600, min_usdc: float = 250.0) -> list[CohortInput]:
    fills = poll_new_activity(lookback_seconds=lookback_seconds, min_usdc=min_usdc)
    if not fills:
        return []
    grouped: dict[str, list] = {}
    for f in fills:
        grouped.setdefault(f.address, []).append(f)
    out: list[CohortInput] = []
    for addr, items in grouped.items():
        lines = [f"[cohort:{addr[:10]}] recent fills:"]
        for f in items[:10]:
            lines.append(
                f"  {f.side} {f.share_amount:,.0f} shares of token {f.token_id[:16]}… "
                f"at ${f.avg_price:.4f} (${f.usdc_amount:,.0f}) at ts={f.timestamp}"
            )
        out.append(CohortInput(text="\n".join(lines)))
    return out
