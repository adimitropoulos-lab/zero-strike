"""Polymarket Gamma API — market and event metadata.

Reference: https://docs.polymarket.com/#gamma
"""
from __future__ import annotations

import json
from typing import Iterable, Iterator

from ..config import settings
from ._http import HttpClient


class GammaClient:
    def __init__(self, base_url: str | None = None):
        self.http = HttpClient(base_url or settings.gamma_url)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def markets(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False,
        tag_id: int | None = None,
    ) -> list[dict]:
        return self.http.get_json(
            "/markets",
            active=str(active).lower(),
            closed=str(closed).lower(),
            limit=limit,
            offset=offset,
            order=order,
            ascending=str(ascending).lower(),
            tag_id=tag_id,
        )

    def iter_markets(self, *, page: int = 200, **kwargs) -> Iterator[dict]:
        offset = 0
        while True:
            batch = self.markets(limit=page, offset=offset, **kwargs)
            if not batch:
                return
            yield from batch
            if len(batch) < page:
                return
            offset += page

    def events(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[dict]:
        return self.http.get_json(
            "/events",
            active=str(active).lower(),
            closed=str(closed).lower(),
            limit=limit,
            offset=offset,
            order=order,
            ascending=str(ascending).lower(),
        )

    def market(self, market_id: str | int) -> dict:
        return self.http.get_json(f"/markets/{market_id}")

    @staticmethod
    def outcome_token_ids(market: dict) -> list[str]:
        """Parse clobTokenIds — Gamma returns it as a JSON-encoded string."""
        raw = market.get("clobTokenIds")
        if not raw:
            return []
        if isinstance(raw, list):
            return [str(t) for t in raw]
        try:
            return [str(t) for t in json.loads(raw)]
        except (ValueError, TypeError):
            return []

    @staticmethod
    def outcome_labels(market: dict) -> list[str]:
        raw = market.get("outcomes")
        if isinstance(raw, list):
            return [str(o) for o in raw]
        try:
            return [str(o) for o in json.loads(raw or "[]")]
        except (ValueError, TypeError):
            return []

    @staticmethod
    def outcome_prices(market: dict) -> list[float]:
        raw = market.get("outcomePrices")
        if isinstance(raw, list):
            return [float(p) for p in raw]
        try:
            return [float(p) for p in json.loads(raw or "[]")]
        except (ValueError, TypeError):
            return []
