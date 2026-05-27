"""Portfolio Kelly — correlation-aware sizing for concurrent bets.

Naive Kelly treats every bet as independent. In practice, Polymarket markets
inside the same event ("Will Trump win?", "Will GOP win popular vote?",
"Will GOP win Senate?") move together — a single news item shifts all three.
Sizing them independently overstates aggregate risk.

We apply two layers of protection:

1. **Hard cluster cap.** No single event can consume more than `cluster_cap_pct`
   of bankroll across all open positions. This is a tail-risk hedge against
   model error correlated across a cluster.

2. **Correlation discount.** Inside the cap, each new bet in a cluster of size
   `n` gets scaled by 1 / (1 + (n-1)·ρ). With ρ=0.5 (our pragmatic default for
   same-event markets), the second bet sizes at 67% of its independent Kelly,
   the third at 50%, etc.

The `kelly_size` function stays simple and independent; portfolio adjustment is
a separate pass that wraps it.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..calibration.store import resolution_store
from ..config import settings
from ..execution.signal import signal_store


@dataclass
class PortfolioAdjustment:
    proposed_dollar: float
    adjusted_dollar: float
    cluster_existing_dollar: float
    cluster_size_after: int
    reasoning: str


def open_exposure_by_event() -> dict[str, list[dict]]:
    """Group unresolved signals by event_id. Signals lacking event_id are skipped."""
    sigs = signal_store.read()
    resolved = {
        k: r
        for k, r in resolution_store.latest_by_signal().items()
        if r.market_closed and r.outcome_indicator is not None
    }
    from ..calibration.resolution import signal_key

    buckets: dict[str, list[dict]] = {}
    for s in sigs:
        if signal_key(s) in resolved:
            continue
        event_id = _signal_event_id(s)
        if not event_id:
            continue
        buckets.setdefault(event_id, []).append(
            {
                "market_id": s.market_id,
                "outcome": s.outcome,
                "side": s.side,
                "dollar_size": s.dollar_size,
                "created_unix": s.created_unix,
            }
        )
    return buckets


def _signal_event_id(signal) -> str | None:
    """Signals don't currently carry event_id natively; we encode it in
    `news_refs` as 'event:<id>' when the tool layer knows it, so we can stay
    backward-compatible with the existing JSONL format."""
    for ref in getattr(signal, "news_refs", []) or []:
        if isinstance(ref, str) and ref.startswith("event:"):
            return ref[len("event:") :]
    return None


def adjust_for_portfolio(
    proposed_dollar: float,
    *,
    event_id: str | None,
    bankroll: float | None = None,
    cluster_cap_pct: float = 0.10,
    rho: float = 0.5,
) -> PortfolioAdjustment:
    """Adjust a proposed dollar size for existing same-event exposure."""
    bankroll = bankroll if bankroll is not None else settings.bankroll

    if proposed_dollar <= 0 or not event_id:
        return PortfolioAdjustment(
            proposed_dollar=proposed_dollar,
            adjusted_dollar=proposed_dollar,
            cluster_existing_dollar=0.0,
            cluster_size_after=1 if proposed_dollar > 0 else 0,
            reasoning="no event_id or zero proposed — passed through",
        )

    existing = open_exposure_by_event().get(event_id, [])
    existing_total = sum(e["dollar_size"] for e in existing)
    n_after = len(existing) + 1

    # Layer 1: hard cluster cap.
    cap = bankroll * cluster_cap_pct
    room = max(0.0, cap - existing_total)
    if room <= 0:
        return PortfolioAdjustment(
            proposed_dollar=proposed_dollar,
            adjusted_dollar=0.0,
            cluster_existing_dollar=existing_total,
            cluster_size_after=n_after,
            reasoning=f"cluster_cap hit: event has ${existing_total:,.0f} ≥ cap ${cap:,.0f}",
        )

    # Layer 2: correlation discount.
    discount = 1.0 / (1.0 + (n_after - 1) * rho)
    discounted = proposed_dollar * discount

    # Final: smaller of (discounted, room).
    final = min(discounted, room)
    reasons = []
    if discount < 1.0:
        reasons.append(
            f"correlation discount: ×{discount:.2f} (cluster size n={n_after}, ρ={rho})"
        )
    if final < discounted:
        reasons.append(
            f"cluster_cap binds: room ${room:,.0f} < discounted ${discounted:,.0f}"
        )
    if not reasons:
        reasons.append("no adjustment needed")

    return PortfolioAdjustment(
        proposed_dollar=proposed_dollar,
        adjusted_dollar=final,
        cluster_existing_dollar=existing_total,
        cluster_size_after=n_after,
        reasoning="; ".join(reasons),
    )
