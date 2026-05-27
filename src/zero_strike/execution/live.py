"""Live order execution via Polymarket CLOB.

Built on `py-clob-client` (Polymarket official) — install with `pip install zero-strike[live]`.
The signing/EIP-712 flow is non-trivial and changes occasionally; delegating to
the official client is safer than rolling our own.

Required env vars (from your proxy wallet setup):
  POLY_PRIVATE_KEY        — EOA private key (signer)
  POLY_FUNDER             — proxy wallet address (the wallet that holds USDC)
  POLY_SIGNATURE_TYPE     — 1 (POLY_PROXY) or 2 (POLY_GNOSIS_SAFE); 0 = EOA-only

Execution is strictly gated:
  - precheck() must pass (caps, halt, daily loss)
  - --live flag must be set
  - signal must have been emitted in the last `signal_max_age_s` seconds
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass

from ..config import settings
from ..execution.signal import Signal, signal_store
from .safety import LiveOrderRecord, precheck, record


@dataclass
class ExecutionResult:
    submitted: bool
    reason: str
    order_id: str | None = None
    raw: dict | None = None


def _live_client():
    """Lazy-import the Polymarket client so the dep stays optional."""
    try:
        from py_clob_client.client import ClobClient as PolyClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError as e:
        raise RuntimeError(
            "py-clob-client not installed. Install live extras: pip install zero-strike[live]"
        ) from e

    pk = os.getenv("POLY_PRIVATE_KEY")
    funder = os.getenv("POLY_FUNDER")
    sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))
    if not pk:
        raise RuntimeError("POLY_PRIVATE_KEY not set — cannot sign live orders.")

    host = settings.clob_url
    chain_id = 137  # Polygon mainnet
    client = PolyClient(host=host, key=pk, chain_id=chain_id, signature_type=sig_type, funder=funder)
    # Bootstrap API credentials (creates if absent).
    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)
    return client, sig_type, funder


def execute_signal(
    signal: Signal,
    *,
    dry_run: bool = True,
    signal_max_age_s: int = 1800,
) -> ExecutionResult:
    """Submit one signal to Polymarket CLOB. `dry_run` prints the order, doesn't send."""
    age = int(time.time()) - signal.created_unix
    if age > signal_max_age_s:
        rec = LiveOrderRecord(
            ts_unix=int(time.time()),
            date=time.strftime("%Y-%m-%d", time.gmtime()),
            market_id=signal.market_id, outcome=signal.outcome, side=signal.side,
            dollar_size=signal.dollar_size, shares=signal.shares, price=signal.p_market,
            submitted=False, reason=f"signal_too_old: {age}s > {signal_max_age_s}s",
        )
        record(rec)
        return ExecutionResult(False, rec.reason)

    ok, reason = precheck(signal.dollar_size)
    if not ok:
        rec = LiveOrderRecord(
            ts_unix=int(time.time()),
            date=time.strftime("%Y-%m-%d", time.gmtime()),
            market_id=signal.market_id, outcome=signal.outcome, side=signal.side,
            dollar_size=signal.dollar_size, shares=signal.shares, price=signal.p_market,
            submitted=False, reason=f"precheck_failed: {reason}",
        )
        record(rec)
        return ExecutionResult(False, rec.reason)

    if dry_run:
        rec = LiveOrderRecord(
            ts_unix=int(time.time()),
            date=time.strftime("%Y-%m-%d", time.gmtime()),
            market_id=signal.market_id, outcome=signal.outcome, side=signal.side,
            dollar_size=signal.dollar_size, shares=signal.shares, price=signal.p_market,
            submitted=False, reason="dry_run",
        )
        record(rec)
        return ExecutionResult(False, "dry_run", raw={"would_submit": True})

    # Real submission — uses the optional py-clob-client dep.
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY, SELL
    except ImportError as e:
        return ExecutionResult(False, f"py-clob-client missing: {e}")

    # We need the actual outcome token id, not the outcome label. Look it up from
    # Gamma using the signal's market_id, then match outcome label → token id.
    from ..polymarket import GammaClient

    with GammaClient() as gamma:
        market = gamma.market(signal.market_id)
        labels = GammaClient.outcome_labels(market)
        tokens = GammaClient.outcome_token_ids(market)
        token_id = None
        for lab, tid in zip(labels, tokens):
            if lab.strip().lower() == signal.outcome.strip().lower():
                token_id = tid
                break
        if token_id is None:
            rec = LiveOrderRecord(
                ts_unix=int(time.time()),
                date=time.strftime("%Y-%m-%d", time.gmtime()),
                market_id=signal.market_id, outcome=signal.outcome, side=signal.side,
                dollar_size=signal.dollar_size, shares=signal.shares, price=signal.p_market,
                submitted=False, reason=f"outcome_label_not_found: '{signal.outcome}' in {labels}",
            )
            record(rec)
            return ExecutionResult(False, rec.reason)

    try:
        client, _, _ = _live_client()
    except Exception as e:
        return ExecutionResult(False, f"client_init_failed: {e}")

    side_const = BUY if signal.side.upper() == "BUY" else SELL
    order_args = OrderArgs(
        price=signal.p_market,
        size=signal.shares,
        side=side_const,
        token_id=token_id,
    )

    try:
        signed = client.create_order(order_args)
        resp = client.post_order(signed, OrderType.GTC)
    except Exception as e:
        rec = LiveOrderRecord(
            ts_unix=int(time.time()),
            date=time.strftime("%Y-%m-%d", time.gmtime()),
            market_id=signal.market_id, outcome=signal.outcome, side=signal.side,
            dollar_size=signal.dollar_size, shares=signal.shares, price=signal.p_market,
            submitted=False, reason=f"submit_error: {type(e).__name__}: {e}",
        )
        record(rec)
        return ExecutionResult(False, rec.reason)

    order_id = (resp or {}).get("orderID") or (resp or {}).get("id")
    rec = LiveOrderRecord(
        ts_unix=int(time.time()),
        date=time.strftime("%Y-%m-%d", time.gmtime()),
        market_id=signal.market_id, outcome=signal.outcome, side=signal.side,
        dollar_size=signal.dollar_size, shares=signal.shares, price=signal.p_market,
        submitted=True, reason="submitted", response=resp,
    )
    record(rec)
    return ExecutionResult(True, "submitted", order_id=order_id, raw=resp)


def execute_recent_signals(
    *, dry_run: bool = True, signal_max_age_s: int = 1800
) -> list[ExecutionResult]:
    """Walk recently-emitted signals once. Caller is expected to call periodically."""
    out: list[ExecutionResult] = []
    now = int(time.time())
    for sig in signal_store.read():
        if now - sig.created_unix > signal_max_age_s:
            continue
        if sig.dollar_size <= 0:
            continue
        out.append(execute_signal(sig, dry_run=dry_run, signal_max_age_s=signal_max_age_s))
    return out
