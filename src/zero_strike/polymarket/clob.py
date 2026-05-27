"""Polymarket CLOB API — order books, prices, midpoints.

Reference: https://docs.polymarket.com/#central-limit-order-book-clob
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from ._http import HttpClient


@dataclass
class FillSimulation:
    """Result of walking the order book to fill a target dollar amount."""
    side: str               # "BUY" (lifting asks) or "SELL" (hitting bids)
    target_dollar: float
    filled_dollar: float
    shares: float
    vwap: float | None
    best_price: float | None
    slippage_bps: float | None   # (vwap - best) / best, in bps; signed (positive = paid up)
    levels_consumed: int
    fully_filled: bool


class ClobClient:
    def __init__(self, base_url: str | None = None):
        self.http = HttpClient(base_url or settings.clob_url)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def midpoint(self, token_id: str) -> float | None:
        try:
            data = self.http.get_json("/midpoint", token_id=token_id)
        except Exception:
            return None
        mid = data.get("mid") if isinstance(data, dict) else None
        return float(mid) if mid is not None else None

    def price(self, token_id: str, side: str = "BUY") -> float | None:
        """Best price the user can `side` (BUY = best ask, SELL = best bid)."""
        try:
            data = self.http.get_json("/price", token_id=token_id, side=side.upper())
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        p = data.get("price")
        return float(p) if p is not None else None

    def book(self, token_id: str) -> dict | None:
        try:
            return self.http.get_json("/book", token_id=token_id)
        except Exception:
            return None

    def prices_for_outcomes(self, token_ids: list[str]) -> dict[str, dict[str, float | None]]:
        """For each outcome token return best ask (cost to buy YES) and best bid."""
        out: dict[str, dict[str, float | None]] = {}
        for tid in token_ids:
            out[tid] = {
                "ask": self.price(tid, "BUY"),
                "bid": self.price(tid, "SELL"),
                "mid": self.midpoint(tid),
            }
        return out

    def book_levels(self, token_id: str, side: str) -> list[tuple[float, float]]:
        """Return [(price, size)] levels sorted to walk in fill order.

        For BUY (lifting asks), ascending price. For SELL (hitting bids), descending.
        """
        book = self.book(token_id) or {}
        side_up = side.upper()
        raw = book.get("asks" if side_up == "BUY" else "bids", [])
        levels: list[tuple[float, float]] = []
        for lvl in raw:
            try:
                p = float(lvl.get("price"))
                s = float(lvl.get("size"))
            except (TypeError, ValueError):
                continue
            if p > 0 and s > 0:
                levels.append((p, s))
        # Polymarket returns asks low-to-high and bids high-to-low natively, but
        # we re-sort defensively in case the API order changes.
        levels.sort(key=lambda x: x[0], reverse=(side_up == "SELL"))
        return levels

    def simulate_fill(self, token_id: str, dollar_target: float, side: str = "BUY") -> FillSimulation:
        """Walk the book to fill `dollar_target` USDC. Returns VWAP and slippage.

        For BUY: spent = sum(price * shares); we want shares such that we spend exactly target.
        For SELL: same accounting — we sell `shares` and collect `target` dollars.
        """
        levels = self.book_levels(token_id, side)
        if not levels:
            return FillSimulation(side.upper(), dollar_target, 0.0, 0.0, None, None, None, 0, False)

        best = levels[0][0]
        spent = 0.0
        shares = 0.0
        consumed = 0
        for price, size in levels:
            consumed += 1
            level_cost = price * size
            if spent + level_cost >= dollar_target:
                remaining = dollar_target - spent
                shares += remaining / price
                spent += remaining
                vwap = spent / shares if shares > 0 else None
                slippage_bps = ((vwap - best) / best * 10_000) if (vwap and best) else None
                if side.upper() == "SELL":
                    slippage_bps = -slippage_bps if slippage_bps is not None else None
                return FillSimulation(
                    side.upper(), dollar_target, spent, shares, vwap, best, slippage_bps,
                    consumed, True,
                )
            spent += level_cost
            shares += size
        # Insufficient depth.
        vwap = spent / shares if shares > 0 else None
        slippage_bps = ((vwap - best) / best * 10_000) if (vwap and best) else None
        if side.upper() == "SELL" and slippage_bps is not None:
            slippage_bps = -slippage_bps
        return FillSimulation(
            side.upper(), dollar_target, spent, shares, vwap, best, slippage_bps,
            consumed, False,
        )
