"""Resolution store — append-only JSONL keyed by `signal_key`.

Kept separate from signals.jsonl so the historical signal record never mutates;
re-resolving the same signal just overwrites the latest entry on read.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import settings


@dataclass
class Resolution:
    signal_key: str         # market_id:created_unix
    market_id: str
    resolved_unix: int
    market_closed: bool     # is the underlying market actually closed?
    outcome_indicator: float | None  # 0.0/1.0 for binary, fractional for partial; None if not resolved
    resolution_price: float | None   # market's settle price for the bet's outcome
    realized_pnl_usd: float
    brier: float | None     # (p_true - outcome_indicator)^2; None if not resolved
    hit: bool | None        # did the directional call pay off?
    notes: str = ""
    written_unix: int = field(default_factory=lambda: int(time.time()))


class ResolutionStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (settings.data_dir / "resolutions.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, resolution: Resolution) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(resolution)) + "\n")

    def read(self) -> list[Resolution]:
        if not self.path.exists():
            return []
        out: list[Resolution] = []
        for line in self.path.read_text().splitlines():
            if not line.strip():
                continue
            out.append(Resolution(**json.loads(line)))
        return out

    def latest_by_signal(self) -> dict[str, Resolution]:
        """Most-recent resolution per signal_key — re-resolves overwrite older ones."""
        latest: dict[str, Resolution] = {}
        for r in self.read():
            cur = latest.get(r.signal_key)
            if cur is None or r.written_unix >= cur.written_unix:
                latest[r.signal_key] = r
        return latest

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


resolution_store = ResolutionStore()
