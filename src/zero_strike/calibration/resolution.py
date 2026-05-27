"""Resolve a signal against its market's current state.

A Polymarket market is considered resolved when Gamma returns `closed=true`
and `outcomePrices` are locked at 0/1 (binary) or fractional (UMA partial).

For each signal we:
  1. Look up the outcome index by label (case-insensitive).
  2. Read that outcome's settle price → `outcome_indicator` ∈ [0, 1].
  3. Compute realized PnL given the signal's side and shares.
  4. Compute Brier = (p_true - outcome_indicator)^2.
"""
from __future__ import annotations

import time

from ..execution.signal import Signal
from ..polymarket import GammaClient
from .store import Resolution


def signal_key(signal: Signal) -> str:
    return f"{signal.market_id}:{signal.created_unix}"


def _match_outcome_index(labels: list[str], wanted: str) -> int | None:
    wanted_norm = wanted.strip().lower()
    for i, lab in enumerate(labels):
        if lab.strip().lower() == wanted_norm:
            return i
    # `emit_signal` sometimes passes comma-joined outcomes for arb signals
    # (e.g. "YES,NO") — for those the bet is on the full set, not one side,
    # so fall through and let the caller treat it as unresolvable per-leg.
    return None


def resolve_signal(signal: Signal, gamma: GammaClient | None = None) -> Resolution:
    own = gamma is None
    gamma = gamma or GammaClient()
    try:
        market = gamma.market(signal.market_id)
    except Exception as e:
        return Resolution(
            signal_key=signal_key(signal),
            market_id=signal.market_id,
            resolved_unix=int(time.time()),
            market_closed=False,
            outcome_indicator=None,
            resolution_price=None,
            realized_pnl_usd=0.0,
            brier=None,
            hit=None,
            notes=f"fetch_error: {type(e).__name__}",
        )
    finally:
        if own:
            gamma.close()

    closed = bool(market.get("closed"))
    labels = GammaClient.outcome_labels(market)
    prices = GammaClient.outcome_prices(market)

    if not closed:
        return Resolution(
            signal_key=signal_key(signal),
            market_id=signal.market_id,
            resolved_unix=int(time.time()),
            market_closed=False,
            outcome_indicator=None,
            resolution_price=None,
            realized_pnl_usd=0.0,
            brier=None,
            hit=None,
            notes="market_open",
        )

    idx = _match_outcome_index(labels, signal.outcome)
    if idx is None or idx >= len(prices):
        return Resolution(
            signal_key=signal_key(signal),
            market_id=signal.market_id,
            resolved_unix=int(time.time()),
            market_closed=True,
            outcome_indicator=None,
            resolution_price=None,
            realized_pnl_usd=0.0,
            brier=None,
            hit=None,
            notes=f"outcome_unmatched: signal='{signal.outcome}' labels={labels}",
        )

    settle = prices[idx]
    indicator = max(0.0, min(1.0, settle))

    # PnL: a BUY at p_market of `shares` shares pays shares*settle, costs dollar_size.
    # A SELL is the inverse (we received dollar_size, owe shares*settle).
    if signal.side.upper() == "BUY":
        realized = signal.shares * settle - signal.dollar_size
    else:
        realized = signal.dollar_size - signal.shares * settle

    brier = (signal.p_true - indicator) ** 2
    hit = realized > 0

    return Resolution(
        signal_key=signal_key(signal),
        market_id=signal.market_id,
        resolved_unix=int(time.time()),
        market_closed=True,
        outcome_indicator=indicator,
        resolution_price=settle,
        realized_pnl_usd=realized,
        brier=brier,
        hit=hit,
        notes="",
    )
