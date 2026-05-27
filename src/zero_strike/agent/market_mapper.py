from __future__ import annotations

from .claude_agent import run_agent_on_news
from .cohort_feed import fetch_cohort_input
from .news_feed import fetch_news


def run_agent(
    *,
    since_seconds: int = 3600,
    max_news: int = 30,
    include_cohort: bool = True,
    verbose: bool = True,
) -> dict:
    """Pull recent news + cohort activity, hand to the agent, return run telemetry."""
    items = fetch_news(since_seconds=since_seconds)[:max_news]
    cohort = fetch_cohort_input(lookback_seconds=since_seconds) if include_cohort else []
    summary = run_agent_on_news(items, cohort_inputs=cohort, verbose=verbose)
    summary["news_items"] = len(items)
    summary["cohort_groups"] = len(cohort)
    return summary
