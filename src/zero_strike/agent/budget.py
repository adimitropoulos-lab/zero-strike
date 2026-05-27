"""Spend tracking + daily cap for Anthropic API usage.

Why: an agent in a tool loop can spin and quietly burn money. We track every
`messages.create` call's usage, multiply by the configured per-model price,
and refuse to start a new run once today's cap is exceeded.

Pricing is configurable per model via env vars (PRICING_<MODEL>_INPUT_PER_MTOK
and _OUTPUT_PER_MTOK). Defaults match published Anthropic prices at time of
writing; override if they change.

Storage: ~/.zero-strike/spend.jsonl, one record per API call.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings


# Default $/M-token. Override via env: PRICING_<UPPER_MODEL_NAME>_INPUT_PER_MTOK
_DEFAULT_PRICES = {
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-7[1m]": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-7": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
_CACHE_READ_DISCOUNT = 0.10   # cached reads typically billed at ~10% of input
_CACHE_WRITE_PREMIUM = 1.25   # cache creation typically billed at ~125%


def _model_key(model: str) -> str:
    return model.upper().replace("-", "_").replace("[", "_").replace("]", "_").replace(".", "_")


def price_per_mtok(model: str) -> tuple[float, float]:
    """Return (input_price_per_mtok, output_price_per_mtok)."""
    key = _model_key(model)
    in_env = os.getenv(f"PRICING_{key}_INPUT_PER_MTOK")
    out_env = os.getenv(f"PRICING_{key}_OUTPUT_PER_MTOK")
    if in_env and out_env:
        return float(in_env), float(out_env)
    return _DEFAULT_PRICES.get(model, (15.00, 75.00))


@dataclass
class UsageRecord:
    ts_unix: int
    date: str                  # YYYY-MM-DD UTC
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cost_usd: float
    purpose: str = "agent"     # tag in case other callers consume the cap

    @classmethod
    def from_usage(cls, model: str, usage, purpose: str = "agent") -> "UsageRecord":
        in_tok = int(getattr(usage, "input_tokens", 0))
        out_tok = int(getattr(usage, "output_tokens", 0))
        read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        p_in, p_out = price_per_mtok(model)
        cost = (
            in_tok * p_in / 1e6
            + out_tok * p_out / 1e6
            + read * p_in * _CACHE_READ_DISCOUNT / 1e6
            + write * p_in * _CACHE_WRITE_PREMIUM / 1e6
        )
        now = int(time.time())
        return cls(
            ts_unix=now,
            date=datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d"),
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=read,
            cache_creation_tokens=write,
            cost_usd=cost,
            purpose=purpose,
        )


class BudgetExceeded(RuntimeError):
    pass


class SpendStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (settings.data_dir / "spend.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: UsageRecord) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    def read(self) -> list[UsageRecord]:
        if not self.path.exists():
            return []
        out: list[UsageRecord] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            out.append(UsageRecord(**json.loads(line)))
        return out

    def spend_today(self) -> float:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        return sum(r.cost_usd for r in self.read() if r.date == today)

    def spend_by_day(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for r in self.read():
            agg[r.date] = agg.get(r.date, 0.0) + r.cost_usd
        return agg


spend_store = SpendStore()


def daily_cap_usd() -> float:
    return float(os.getenv("DAILY_SPEND_CAP_USD", "25.0"))


def check_budget() -> tuple[bool, str]:
    """True if there's room under today's cap. Reason populated when False."""
    cap = daily_cap_usd()
    spent = spend_store.spend_today()
    if spent >= cap:
        return False, f"daily spend ${spent:.2f} >= cap ${cap:.2f}"
    return True, f"daily spend ${spent:.2f} / cap ${cap:.2f}"


def record_usage(model: str, usage, purpose: str = "agent") -> UsageRecord:
    rec = UsageRecord.from_usage(model, usage, purpose=purpose)
    spend_store.append(rec)
    return rec
