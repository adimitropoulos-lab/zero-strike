from __future__ import annotations

from .claude_agent import run_agent_on_news
from .news_feed import fetch_news


def run_agent(*, since_seconds: int = 3600, max_news: int = 30, verbose: bool = True) -> dict:
    """Pull recent news, hand to the agent, return run telemetry."""
    items = fetch_news(since_seconds=since_seconds)[:max_news]
    summary = run_agent_on_news(items, verbose=verbose)
    summary["news_items"] = len(items)
    return summary
