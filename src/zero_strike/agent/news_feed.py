"""RSS news feed — stdlib XML parsing (feedparser pulls a broken build dep).

We handle RSS 2.0 and Atom 1.0 — enough for the major newswires.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from ..config import settings


@dataclass
class NewsItem:
    title: str
    summary: str
    link: str
    source: str
    published_unix: int

    def as_text(self) -> str:
        return f"[{self.source}] {self.title}\n  {self.summary}\n  ({self.link})"


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _strip_html(text: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", text or "")).strip()


def _parse_date(s: str | None) -> int:
    if not s:
        return int(time.time())
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return int(time.mktime(time.strptime(s, fmt)))
        except ValueError:
            continue
    return int(time.time())


def _parse_feed(xml_text: str, fallback_source: str) -> list[NewsItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # Atom feeds have a default namespace; rss does not.
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items: list[NewsItem] = []

    # RSS 2.0
    channel = root.find("channel")
    if channel is not None:
        source = (channel.findtext("title") or fallback_source).strip()
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = _strip_html(item.findtext("description") or "")
            pub = item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
            items.append(NewsItem(title, desc[:500], link, source, _parse_date(pub)))
        return items

    # Atom 1.0
    if root.tag.endswith("feed"):
        source = (root.findtext("atom:title", namespaces=ns) or fallback_source).strip()
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href") if link_el is not None else ""
            summary = _strip_html(
                entry.findtext("atom:summary", namespaces=ns)
                or entry.findtext("atom:content", namespaces=ns)
                or ""
            )
            pub = entry.findtext("atom:updated", namespaces=ns) or entry.findtext(
                "atom:published", namespaces=ns
            )
            items.append(NewsItem(title, summary[:500], link, source, _parse_date(pub)))
    return items


def fetch_news(
    *, feeds: list[str] | None = None, max_per_feed: int = 25, since_seconds: int = 3600
) -> list[NewsItem]:
    feeds = feeds or list(settings.rss_feeds)
    cutoff = time.time() - since_seconds
    out: list[NewsItem] = []
    with httpx.Client(timeout=15.0, headers={"User-Agent": "zero-strike/0.1"}) as c:
        for url in feeds:
            try:
                r = c.get(url)
                r.raise_for_status()
            except Exception:
                continue
            for item in _parse_feed(r.text, fallback_source=url)[:max_per_feed]:
                if item.published_unix < cutoff:
                    continue
                out.append(item)
    out.sort(key=lambda x: x.published_unix, reverse=True)
    return out
