"""Twitter/X news source — requires TWITTER_BEARER_TOKEN. Silent no-op otherwise."""
from __future__ import annotations

import os
import time

import httpx

from ..news_feed import NewsItem


def fetch_twitter(
    *,
    query: str | None = None,
    limit: int = 25,
    since_seconds: int = 3600,
) -> list[NewsItem]:
    token = os.getenv("TWITTER_BEARER_TOKEN")
    if not token:
        return []
    query = query or os.getenv("TWITTER_QUERY", "(breaking OR developing) -is:retweet lang:en")
    out: list[NewsItem] = []
    cutoff = time.time() - since_seconds
    try:
        with httpx.Client(
            timeout=15.0,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "zero-strike/0.1"},
        ) as c:
            r = c.get(
                "https://api.twitter.com/2/tweets/search/recent",
                params={
                    "query": query,
                    "max_results": min(100, limit),
                    "tweet.fields": "created_at,author_id",
                },
            )
            r.raise_for_status()
            data = r.json().get("data", [])
    except Exception:
        return []
    for t in data:
        ts = int(time.time())
        if t.get("created_at"):
            try:
                ts = int(
                    time.mktime(time.strptime(t["created_at"], "%Y-%m-%dT%H:%M:%S.000Z"))
                )
            except ValueError:
                pass
        if ts < cutoff:
            continue
        text = t.get("text", "").strip()
        out.append(
            NewsItem(
                title=text[:140],
                summary=text[:500],
                link=f"https://x.com/i/web/status/{t.get('id', '')}",
                source=f"x:{t.get('author_id', '')}",
                published_unix=ts,
            )
        )
    return out
