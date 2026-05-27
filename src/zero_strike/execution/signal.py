"""Signal recording — paper trade only.

This deliberately stops at recording. Actual order submission needs the user's
Polymarket proxy wallet keys and EOA signature flow; that's a separate concern
the operator wires up themselves. The signal log is the agent's deliverable.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from ..config import settings


@dataclass
class Signal:
    market_id: str
    question: str
    outcome: str
    side: str
    p_true: float
    p_market: float
    shares: float
    dollar_size: float
    edge_bps: float
    rationale: str
    news_refs: list[str] = field(default_factory=list)
    expires_in_minutes: int = 60
    created_unix: int = field(default_factory=lambda: int(time.time()))


class SignalStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (settings.data_dir / "signals.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, signal: Signal) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(signal)) + "\n")

    def read(self) -> list[Signal]:
        if not self.path.exists():
            return []
        out: list[Signal] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            out.append(Signal(**data))
        return out

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


signal_store = SignalStore()
