"""Persistent cohort store — the 7 wallets currently being mirrored."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import settings


@dataclass
class CohortMember:
    address: str
    composite_z: float
    win_rate: float
    n_trades: int
    pnl_usd: float
    added_unix: int = field(default_factory=lambda: int(time.time()))


@dataclass
class CohortSnapshot:
    selected_unix: int
    lookback_days: int
    members: list[CohortMember]


class CohortStore:
    def __init__(self, path: Path | None = None, *, last_seen_path: Path | None = None):
        self.path = path or (settings.data_dir / "cohort.json")
        self.last_seen_path = last_seen_path or (settings.data_dir / "cohort_last_seen.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: CohortSnapshot) -> None:
        self.path.write_text(json.dumps(asdict(snapshot), indent=2))

    def load(self) -> CohortSnapshot | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text())
        return CohortSnapshot(
            selected_unix=data["selected_unix"],
            lookback_days=data["lookback_days"],
            members=[CohortMember(**m) for m in data["members"]],
        )

    def addresses(self) -> list[str]:
        snap = self.load()
        return [m.address for m in snap.members] if snap else []

    def get_last_seen(self) -> dict[str, int]:
        if not self.last_seen_path.exists():
            return {}
        return json.loads(self.last_seen_path.read_text())

    def set_last_seen(self, last_seen: dict[str, int]) -> None:
        self.last_seen_path.write_text(json.dumps(last_seen))


cohort_store = CohortStore()
