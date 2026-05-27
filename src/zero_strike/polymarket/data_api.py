"""Polymarket Data API — trades, positions, activity.

Reference: https://docs.polymarket.com/developers/data-api
"""
from __future__ import annotations

from ..config import settings
from ._http import HttpClient


class DataApiClient:
    def __init__(self, base_url: str | None = None):
        self.http = HttpClient(base_url or settings.data_url)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def trades(
        self,
        *,
        user: str | None = None,
        market: str | None = None,
        limit: int = 500,
        offset: int = 0,
        side: str | None = None,
        filter_type: str | None = None,
        filter_amount: float | None = None,
    ) -> list[dict]:
        params = {
            "user": user,
            "market": market,
            "limit": limit,
            "offset": offset,
            "side": side,
            "filterType": filter_type,
            "filterAmount": filter_amount,
            "takerOnly": "false",
        }
        try:
            return self.http.get_json("/trades", **params) or []
        except Exception:
            return []

    def positions(self, user: str, *, limit: int = 200, offset: int = 0) -> list[dict]:
        try:
            return self.http.get_json("/positions", user=user, limit=limit, offset=offset) or []
        except Exception:
            return []

    def activity(self, user: str, *, limit: int = 500, offset: int = 0) -> list[dict]:
        try:
            return self.http.get_json("/activity", user=user, limit=limit, offset=offset) or []
        except Exception:
            return []

    def holders(self, market: str) -> list[dict]:
        try:
            return self.http.get_json("/holders", market=market) or []
        except Exception:
            return []

    def value(self, user: str) -> float | None:
        try:
            data = self.http.get_json("/value", user=user)
        except Exception:
            return None
        if isinstance(data, list) and data:
            return float(data[0].get("value", 0))
        if isinstance(data, dict):
            return float(data.get("value", 0))
        return None
