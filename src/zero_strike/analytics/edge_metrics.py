"""Edge metrics for a trader's recent history.

We compute four orthogonal signals:

1. **win_rate**            — share of closed positions with realized PnL > 0
2. **sizing_discipline**   — 1 - Gini(position_size); higher = more uniform sizing
                              (whales who bet everything on one market score low)
3. **entry_timing_z**      — average z-score of entry prices vs. the eventual
                              resolution price, normalized to (-1, 1). Positive
                              means they enter when the market is *underpricing*
                              the eventual outcome.
4. **risk_adjusted_edge**  — pnl / std(per-trade pnl), Sharpe-style. Captures
                              consistency rather than one fat tail.

The four are combined via z-score sum into an `edge_score`. The selector then
picks wallets that score high on *multiple* axes — that's what makes the edge
repeatable as opposed to a lucky concentration bet.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np


@dataclass
class EdgeReport:
    address: str
    n_trades: int
    win_rate: float
    sizing_discipline: float
    entry_timing: float
    risk_adjusted_edge: float
    edge_score: float
    pnl_usd: float
    volume_usd: float

    def as_row(self) -> dict:
        return self.__dict__.copy()


def _gini(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = np.array(sorted(abs(v) for v in values), dtype=float)
    if arr.sum() == 0:
        return 0.0
    n = len(arr)
    cum = np.cumsum(arr)
    return (n + 1 - 2 * np.sum(cum) / cum[-1]) / n


def _trade_pnls(fills: list[dict]) -> list[dict]:
    """Reduce raw OrderFilled rows into per-market position outcomes.

    A position is keyed by (maker, makerAssetId) — we accumulate the USDC paid
    and shares received, then a closing trade in the opposite direction realizes
    PnL. Anything still open is excluded from win-rate but counted in volume.
    """
    book: dict[tuple[str, str], dict] = {}
    closed: list[dict] = []
    for f in sorted(fills, key=lambda x: int(x.get("timestamp", 0))):
        maker = (f.get("maker") or "").lower()
        # In Polymarket fills, USDC has assetId == "0" (the collateral side).
        # When makerAssetId == "0" the maker is BUYING outcome shares; when
        # takerAssetId == "0" the maker is SELLING outcome shares.
        maker_asset = str(f.get("makerAssetId", "0"))
        taker_asset = str(f.get("takerAssetId", "0"))
        m_amt = float(f.get("makerAmountFilled", 0)) / 1e6
        t_amt = float(f.get("takerAmountFilled", 0)) / 1e6
        ts = int(f.get("timestamp", 0))
        if maker_asset == "0" and taker_asset != "0":
            # Buying `taker_asset` shares for m_amt USDC
            key = (maker, taker_asset)
            pos = book.setdefault(key, {"cost": 0.0, "shares": 0.0, "opened": ts, "entries": []})
            pos["cost"] += m_amt
            pos["shares"] += t_amt
            pos["entries"].append({"price": m_amt / t_amt if t_amt else 0.0, "ts": ts, "size": m_amt})
        elif taker_asset == "0" and maker_asset != "0":
            # Selling `maker_asset` shares for t_amt USDC
            key = (maker, maker_asset)
            pos = book.get(key)
            if pos and pos["shares"] > 0:
                share_sold = min(m_amt, pos["shares"])
                cost_basis = (share_sold / pos["shares"]) * pos["cost"] if pos["shares"] else 0.0
                proceeds = (share_sold / m_amt) * t_amt if m_amt else 0.0
                pnl = proceeds - cost_basis
                closed.append({
                    "user": maker,
                    "token": maker_asset,
                    "pnl": pnl,
                    "size": cost_basis,
                    "entry_price": (pos["cost"] / pos["shares"]) if pos["shares"] else 0.0,
                    "exit_price": (t_amt / m_amt) if m_amt else 0.0,
                    "opened": pos["opened"],
                    "closed": ts,
                })
                pos["shares"] -= share_sold
                pos["cost"] -= cost_basis
    return closed


def compute_edge(address: str, fills: list[dict]) -> EdgeReport:
    """Compute edge metrics from raw subgraph fills for one wallet."""
    closed = _trade_pnls(fills)
    n = len(closed)
    if n == 0:
        return EdgeReport(address, 0, 0, 0, 0, 0, 0, 0, 0)

    pnls = [t["pnl"] for t in closed]
    sizes = [t["size"] for t in closed]
    entries = [t["entry_price"] for t in closed]
    exits = [t["exit_price"] for t in closed]

    pnl_total = sum(pnls)
    volume = sum(sizes) + sum(t.get("size", 0) + abs(t.get("pnl", 0)) for t in closed)
    win_rate = sum(1 for p in pnls if p > 0) / n

    sizing_discipline = 1.0 - _gini(sizes)

    # Entry timing: did they buy below where the position later marked? Compare
    # entry price to exit price. For shares that resolved profitably, lower
    # entry = better timing. Normalize to [-1, 1] via tanh.
    timing_signal = (
        statistics.mean((e_x - e_n) for e_n, e_x in zip(entries, exits)) if n else 0.0
    )
    entry_timing = math.tanh(timing_signal * 5)

    if n >= 2:
        sd = statistics.pstdev(pnls)
        risk_adjusted = (statistics.mean(pnls) / sd) if sd > 0 else 0.0
    else:
        risk_adjusted = 0.0

    # Composite — equal-weighted z-style combination (we'll z-score across wallets
    # in the selection step; here we just expose the raw axes plus a heuristic sum).
    edge_score = (
        win_rate
        + sizing_discipline
        + (entry_timing + 1) / 2
        + math.tanh(risk_adjusted)
    )

    return EdgeReport(
        address=address,
        n_trades=n,
        win_rate=win_rate,
        sizing_discipline=sizing_discipline,
        entry_timing=entry_timing,
        risk_adjusted_edge=risk_adjusted,
        edge_score=edge_score,
        pnl_usd=pnl_total,
        volume_usd=volume,
    )
