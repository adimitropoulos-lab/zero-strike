"""Bluesky news source — public searchPosts REST endpoint, no auth required."""
from __future__ import annotations

import os
import time
from email.utils import parsedate_to_datetime

import httpx

from ..news_feed import NewsItem


_DEFAULT_QUERIES = ("breaking", "developing")
_API = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"


def _parse_ts(iso: str | None) -> int:
    if not iso:
        return int(time.time())
    try:
        return int(parsedate_to_datetime(iso).timestamp())
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return int(time.mktime(time.strptime(iso, fmt)))
        except ValueError:
            continue
    return int(time.time())


def fetch_bluesky(
    *,
    queries: list[str] | None = None,
    limit_per_query: int = 25,
    since_seconds: int = 3600,
) -> list[NewsItem]:
    queries = queries or list(
        q.strip()
        for q in os.getenv("BLUESKY_QUERIES", ",".join(_DEFAULT_QUERIES)).split(",")
        if q.strip()
    )
    cutoff = time.time() - since_seconds
    out: list[NewsItem] = []
    with httpx.Client(timeout=15.0, headers={"User-Agent": "zero-strike/0.1"}) as c:
        for q in queries:
            try:
                r = c.get(_API, params={"q": q, "limit": limit_per_query, "sort": "latest"})
                r.raise_for_status()
            except Exception:
                continue
            for post in r.json().get("posts", []):
                record = post.get("record") or {}
                text = (record.get("text") or "").strip()
                if not text:
                    continue
                ts = _parse_ts(record.get("createdAt"))
                if ts < cutoff:
                    continue
                author = (post.get("author") or {}).get("handle") or "bluesky"
                uri = post.get("uri", "")
                # Build a web URL: at://did:.../app.bsky.feed.post/RKEY → bsky.app/profile/.../post/RKEY
                link = uri
                if uri.startswith("at://"):
                    parts = uri[5:].split("/")
                    if len(parts) >= 3 and parts[1] == "app.bsky.feed.post":
                        link = f"https://bsky.app/profile/{parts[0]}/post/{parts[2]}"
                out.append(
                    NewsItem(
                        title=text[:140],
                        summary=text[:500],
                        link=link,
                        source=f"bsky:{author}",
                        published_unix=ts,
                    )
                )
    out.sort(key=lambda x: x.published_unix, reverse=True)
    return out
