"""Kelly criterion for Polymarket binary outcomes.

For a YES contract priced `p_market` ∈ (0,1):
  - You pay p_market, receive 1 if YES resolves, 0 otherwise.
  - That's a bet at decimal odds  o = 1 / p_market  (net odds b = o - 1).
  - With your subjective probability p_true, the Kelly fraction of bankroll is:
        f* = (b·p - q) / b    where  q = 1 - p,  b = (1 / p_market) - 1
    which simplifies to  f* = (p_true - p_market) / (1 - p_market).

Fractional Kelly multiplier and a hard position cap protect against model error
(p_true is an estimate, not ground truth).
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings


@dataclass
class KellyResult:
    edge: float                # p_true - p_market (negative = pass)
    full_kelly_fraction: float # f* of bankroll
    scaled_fraction: float     # after Kelly multiplier + hard cap
    dollar_size: float
    shares: float
    expected_value: float      # E[$] of one share = p_true - p_market
    reasoning: str


def kelly_fraction(p_true: float, p_market: float) -> float:
    p_true = max(min(p_true, 0.999), 0.001)
    p_market = max(min(p_market, 0.999), 0.001)
    if p_true <= p_market:
        return 0.0
    return (p_true - p_market) / (1 - p_market)


def kelly_size(
    p_true: float,
    p_market: float,
    *,
    bankroll: float | None = None,
    kelly_multiplier: float | None = None,
    max_position_pct: float | None = None,
    min_edge_bps: int | None = None,
) -> KellyResult:
    bankroll = bankroll if bankroll is not None else settings.bankroll
    kelly_multiplier = kelly_multiplier if kelly_multiplier is not None else settings.kelly_fraction
    max_position_pct = max_position_pct if max_position_pct is not None else settings.max_position_pct
    min_edge_bps = min_edge_bps if min_edge_bps is not None else settings.min_edge_bps

    edge = p_true - p_market
    f_full = kelly_fraction(p_true, p_market)
    f_scaled = min(f_full * kelly_multiplier, max_position_pct)

    if edge * 10_000 < min_edge_bps:
        reason = (
            f"edge {edge*10_000:.0f} bps below threshold {min_edge_bps} bps — "
            "no bet (model error overwhelms thin edge)"
        )
        return KellyResult(edge, f_full, 0.0, 0.0, 0.0, edge, reason)

    if f_full <= 0:
        return KellyResult(edge, 0.0, 0.0, 0.0, 0.0, edge, "no edge — market ≥ subjective")

    dollar = bankroll * f_scaled
    shares = dollar / p_market
    reason = (
        f"full Kelly={f_full:.3f}, scaled by {kelly_multiplier} → {f_scaled:.3f} of bankroll, "
        f"capped at {max_position_pct:.3f}; EV per $1 staked = {edge / p_market:+.3f}"
    )
    return KellyResult(edge, f_full, f_scaled, dollar, shares, edge, reason)
