"""Circuit breakers for live execution.

Three gates, all of which must pass before an order is submitted:

  1. Per-order cap   — no single order exceeds MAX_ORDER_USD
  2. Daily loss cap  — once today's realized PnL goes below -DAILY_LOSS_CAP, halt
  3. Daily volume cap — total notional submitted today ≤ MAX_DAILY_VOLUME_USD

Halt state is persistent (~/.zero-strike/halt.flag) — operator must `unhalt` to resume.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings


_HALT_FLAG = settings.data_dir / "halt.flag"
_LIVE_LOG = settings.data_dir / "live_orders.jsonl"


def max_order_usd() -> float:
    return float(os.getenv("MAX_ORDER_USD", "500"))


def daily_loss_cap_usd() -> float:
    return float(os.getenv("DAILY_LOSS_CAP_USD", "200"))


def max_daily_volume_usd() -> float:
    return float(os.getenv("MAX_DAILY_VOLUME_USD", "5000"))


@dataclass
class LiveOrderRecord:
    ts_unix: int
    date: str
    market_id: str
    outcome: str
    side: str
    dollar_size: float
    shares: float
    price: float
    submitted: bool
    reason: str = ""
    response: dict | None = None


def is_halted() -> tuple[bool, str]:
    if _HALT_FLAG.exists():
        return True, _HALT_FLAG.read_text().strip() or "halted"
    return False, ""


def halt(reason: str) -> None:
    _HALT_FLAG.write_text(f"{int(time.time())}: {reason}")


def unhalt() -> None:
    if _HALT_FLAG.exists():
        _HALT_FLAG.unlink()


def _today_volume_usd() -> float:
    if not _LIVE_LOG.exists():
        return 0.0
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    for line in _LIVE_LOG.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("date") == today and rec.get("submitted"):
            total += float(rec.get("dollar_size", 0))
    return total


def _today_realized_pnl() -> float:
    """Sum of resolutions that closed today — proxy for daily realized PnL."""
    # Lazy import to avoid circular dep with calibration → execution.signal.
    from ..calibration import resolution_store

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    total = 0.0
    for r in resolution_store.read():
        if not r.market_closed or r.realized_pnl_usd is None:
            continue
        if datetime.fromtimestamp(r.resolved_unix, tz=timezone.utc).strftime("%Y-%m-%d") == today:
            total += r.realized_pnl_usd
    return total


def precheck(dollar_size: float) -> tuple[bool, str]:
    halted, reason = is_halted()
    if halted:
        return False, f"halted: {reason}"
    if dollar_size <= 0:
        return False, "dollar_size <= 0"
    if dollar_size > max_order_usd():
        return False, f"dollar_size ${dollar_size:.0f} > per-order cap ${max_order_usd():.0f}"
    vol_today = _today_volume_usd()
    if vol_today + dollar_size > max_daily_volume_usd():
        return False, (
            f"would breach daily volume cap: ${vol_today:.0f} + ${dollar_size:.0f} > "
            f"${max_daily_volume_usd():.0f}"
        )
    realized = _today_realized_pnl()
    if realized < -daily_loss_cap_usd():
        halt(f"daily realized PnL ${realized:.0f} ≤ -${daily_loss_cap_usd():.0f}")
        return False, f"daily loss cap auto-halt at ${realized:.0f}"
    return True, "ok"


def record(order: LiveOrderRecord) -> None:
    _LIVE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LIVE_LOG.open("a") as f:
        f.write(json.dumps(asdict(order)) + "\n")


def read_records() -> list[LiveOrderRecord]:
    if not _LIVE_LOG.exists():
        return []
    out: list[LiveOrderRecord] = []
    for line in _LIVE_LOG.read_text().splitlines():
        if not line.strip():
            continue
        out.append(LiveOrderRecord(**json.loads(line)))
    return out
