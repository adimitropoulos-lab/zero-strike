"""Poll the cohort's subgraph fills for new positions since last seen."""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..polymarket import SubgraphClient
from .cohort_store import cohort_store


@dataclass
class CohortFill:
    address: str
    timestamp: int
    side: str               # BUY (acquired shares) / SELL (closed shares)
    token_id: str
    usdc_amount: float
    share_amount: float
    avg_price: float


def _classify_fill(fill: dict, member: str) -> CohortFill | None:
    maker = (fill.get("maker") or "").lower()
    if maker != member.lower():
        return None
    maker_asset = str(fill.get("makerAssetId", "0"))
    taker_asset = str(fill.get("takerAssetId", "0"))
    m_amt = float(fill.get("makerAmountFilled", 0)) / 1e6
    t_amt = float(fill.get("takerAmountFilled", 0)) / 1e6
    ts = int(fill.get("timestamp", 0))
    if maker_asset == "0" and taker_asset != "0":
        return CohortFill(
            address=member, timestamp=ts, side="BUY",
            token_id=taker_asset, usdc_amount=m_amt, share_amount=t_amt,
            avg_price=(m_amt / t_amt) if t_amt else 0.0,
        )
    if taker_asset == "0" and maker_asset != "0":
        return CohortFill(
            address=member, timestamp=ts, side="SELL",
            token_id=maker_asset, usdc_amount=t_amt, share_amount=m_amt,
            avg_price=(t_amt / m_amt) if m_amt else 0.0,
        )
    return None


def poll_new_activity(*, lookback_seconds: int = 3600, min_usdc: float = 250.0) -> list[CohortFill]:
    """Return cohort fills since last poll. Updates last-seen watermark per member."""
    members = cohort_store.addresses()
    if not members:
        return []

    last_seen = cohort_store.get_last_seen()
    now = int(time.time())
    cutoff_default = now - lookback_seconds

    out: list[CohortFill] = []
    with SubgraphClient() as sg:
        for addr in members:
            since = max(last_seen.get(addr, cutoff_default), cutoff_default)
            try:
                rows = sg.trader_fills(addr, since_unix=since)
            except Exception:
                continue
            newest_ts = since
            for row in rows:
                f = _classify_fill(row, addr)
                if f is None:
                    continue
                if f.usdc_amount < min_usdc:
                    continue
                out.append(f)
                newest_ts = max(newest_ts, f.timestamp)
            last_seen[addr] = newest_ts

    cohort_store.set_last_seen(last_seen)
    out.sort(key=lambda x: x.timestamp, reverse=True)
    return out
