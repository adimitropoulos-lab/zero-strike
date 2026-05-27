"""Polymarket CLOB API — order books, prices, midpoints.

Reference: https://docs.polymarket.com/#central-limit-order-book-clob
"""
from __future__ import annotations

from ..config import settings
from ._http import HttpClient


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
