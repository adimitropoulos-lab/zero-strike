from __future__ import annotations

import time
from dataclasses import dataclass

from ..polymarket import SubgraphClient


@dataclass
class Wallet:
    address: str
    pnl_usd: float
    volume_usd: float
    positions: int


def scan_top_wallets(*, days: int = 90, limit: int = 200) -> list[Wallet]:
    """Pull the top-PnL wallets active in the last `days` window."""
    since = int(time.time()) - days * 86_400
    with SubgraphClient() as sg:
        rows = sg.top_traders(since_unix=since, limit=limit)
    return [
        Wallet(
            address=r["user"],
            pnl_usd=r["pnl"],
            volume_usd=r["bought"] + r["sold"],
            positions=r["positions"],
        )
        for r in rows
    ]
