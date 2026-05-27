"""Goldsky Polymarket subgraph — historical trader activity (PnL, FilledOrders).

The subgraph schema exposes `userPositions`, `fpmmTrades`, and `orderFilledEvents` —
we use it to enumerate top wallets by realized PnL and pull trade timelines.
"""
from __future__ import annotations

from ..config import settings
from ._http import HttpClient


_TOP_TRADERS = """
query TopTraders($since: BigInt!, $first: Int!, $skip: Int!) {
  userPositions: userPositions(
    first: $first
    skip: $skip
    orderBy: realizedPnl
    orderDirection: desc
    where: { realizedPnl_gt: 0, lastTimestamp_gte: $since }
  ) {
    id
    user { id }
    realizedPnl
    totalBought
    totalSold
    lastTimestamp
  }
}
"""

_TRADER_FILLS = """
query TraderFills($user: Bytes!, $since: BigInt!, $first: Int!, $skip: Int!) {
  orderFilledEvents(
    first: $first
    skip: $skip
    where: { maker: $user, timestamp_gte: $since }
    orderBy: timestamp
    orderDirection: asc
  ) {
    id
    timestamp
    maker
    taker
    makerAssetId
    takerAssetId
    makerAmountFilled
    takerAmountFilled
    fee
  }
}
"""


class SubgraphClient:
    def __init__(self, url: str | None = None):
        self.http = HttpClient(url or settings.subgraph_url)

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def query(self, query: str, variables: dict) -> dict:
        data = self.http.post_json("", {"query": query, "variables": variables})
        if "errors" in data:
            raise RuntimeError(f"subgraph error: {data['errors']}")
        return data.get("data", {})

    def top_traders(self, *, since_unix: int, limit: int = 200) -> list[dict]:
        """Aggregate realized PnL by wallet across positions touched since `since_unix`."""
        agg: dict[str, dict] = {}
        skip = 0
        page = 1000
        while skip < 10_000:
            try:
                data = self.query(
                    _TOP_TRADERS,
                    {"since": str(since_unix), "first": page, "skip": skip},
                )
            except Exception:
                break
            rows = data.get("userPositions", [])
            if not rows:
                break
            for r in rows:
                u = (r.get("user") or {}).get("id") or r.get("id", "").split("-")[0]
                if not u:
                    continue
                e = agg.setdefault(u, {"user": u, "pnl": 0.0, "bought": 0.0, "sold": 0.0, "positions": 0})
                e["pnl"] += float(r.get("realizedPnl", 0)) / 1e6
                e["bought"] += float(r.get("totalBought", 0)) / 1e6
                e["sold"] += float(r.get("totalSold", 0)) / 1e6
                e["positions"] += 1
            if len(rows) < page:
                break
            skip += page
        ranked = sorted(agg.values(), key=lambda x: x["pnl"], reverse=True)
        return ranked[:limit]

    def trader_fills(self, user: str, *, since_unix: int, max_rows: int = 5000) -> list[dict]:
        out: list[dict] = []
        skip = 0
        page = 1000
        while len(out) < max_rows:
            try:
                data = self.query(
                    _TRADER_FILLS,
                    {"user": user.lower(), "since": str(since_unix), "first": page, "skip": skip},
                )
            except Exception:
                break
            rows = data.get("orderFilledEvents", [])
            if not rows:
                break
            out.extend(rows)
            if len(rows) < page:
                break
            skip += page
        return out[:max_rows]
