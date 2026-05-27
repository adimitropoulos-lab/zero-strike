"""Tool implementations the Anthropic agent calls during its loop.

Each tool is (a) declared as a JSON schema for the Messages API, and
(b) executed locally when Claude returns a `tool_use` block.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..arb.scanner import _check_market
from ..config import settings
from ..execution.signal import Signal, signal_store
from ..polymarket import ClobClient, GammaClient
from ..sizing.kelly import kelly_size
from ..sizing.portfolio import adjust_for_portfolio


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_markets",
        "description": (
            "Search active Polymarket markets by free-text query. Returns a list of markets "
            "with their id, question, end date, 24h volume, outcomes, and current outcome prices."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text query — keywords from a news event."},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 25},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_market_prices",
        "description": (
            "Return current best bid, best ask, and midpoint for every outcome of a Polymarket "
            "market. Use immediately before sizing a bet — the search_markets prices may be stale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {"type": "string", "description": "Polymarket numeric market id or condition id."},
            },
            "required": ["market_id"],
        },
    },
    {
        "name": "check_arbitrage",
        "description": (
            "Check a market for risk-free arb (sum of outcome best-asks < 1 or best-bids > 1). "
            "Returns the arb opportunity if one exists, else null."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {"type": "string"},
            },
            "required": ["market_id"],
        },
    },
    {
        "name": "size_with_kelly",
        "description": (
            "Compute Kelly-criterion position size given your subjective probability and the "
            "market's current price for the same outcome. Uses configured bankroll, Kelly "
            "multiplier, hard position cap, and minimum-edge threshold. If you pass "
            "`event_id`, the size is additionally adjusted for correlated open exposure in "
            "the same Polymarket event (cluster cap + correlation discount)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "p_true": {"type": "number", "minimum": 0, "maximum": 1, "description": "Your subjective probability of YES."},
                "p_market": {"type": "number", "minimum": 0, "maximum": 1, "description": "Market price of YES (best ask)."},
                "outcome_label": {"type": "string", "description": "Outcome you're betting on, e.g. YES or 'Trump'."},
                "event_id": {"type": "string", "description": "Polymarket event_id (from search_markets or get_market_prices). Pass it whenever known so correlated bets are sized correctly."},
            },
            "required": ["p_true", "p_market"],
        },
    },
    {
        "name": "emit_signal",
        "description": (
            "Record a trade signal — this is the agent's final output. Use only after you've "
            "verified prices and sized with Kelly. Pass `event_id` whenever known so future "
            "correlated bets get sized against this position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {"type": "string"},
                "event_id": {"type": "string", "description": "Polymarket event_id for portfolio clustering."},
                "question": {"type": "string"},
                "outcome": {"type": "string", "description": "Outcome being bought, e.g. YES."},
                "side": {"type": "string", "enum": ["BUY", "SELL"]},
                "p_true": {"type": "number"},
                "p_market": {"type": "number"},
                "shares": {"type": "number"},
                "dollar_size": {"type": "number"},
                "edge_bps": {"type": "number"},
                "rationale": {"type": "string", "description": "≤200 words why this signal is real, not noise."},
                "news_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "URLs of news items that triggered the signal.",
                },
                "expires_in_minutes": {"type": "integer", "default": 60},
            },
            "required": [
                "market_id",
                "question",
                "outcome",
                "side",
                "p_true",
                "p_market",
                "shares",
                "dollar_size",
                "edge_bps",
                "rationale",
            ],
        },
    },
]


# Shared client handles so we don't churn TCP connections inside the loop.
_gamma: GammaClient | None = None
_clob: ClobClient | None = None


def _gamma_client() -> GammaClient:
    global _gamma
    if _gamma is None:
        _gamma = GammaClient()
    return _gamma


def _clob_client() -> ClobClient:
    global _clob
    if _clob is None:
        _clob = ClobClient()
    return _clob


def close_clients() -> None:
    global _gamma, _clob
    if _gamma is not None:
        _gamma.close()
        _gamma = None
    if _clob is not None:
        _clob.close()
        _clob = None


def _extract_event_id(market: dict) -> str | None:
    """Gamma returns event linkage as `events: [{id, title, ...}]` (sometimes
    just `event`). Use the first event's id — that's the cluster boundary."""
    events = market.get("events") or market.get("event")
    if isinstance(events, list) and events:
        e = events[0]
        if isinstance(e, dict):
            return str(e.get("id") or e.get("eventId") or "") or None
    if isinstance(events, dict):
        return str(events.get("id") or "") or None
    eid = market.get("eventId") or market.get("event_id")
    return str(eid) if eid else None


def _market_summary(market: dict) -> dict:
    return {
        "id": str(market.get("id") or market.get("conditionId") or ""),
        "event_id": _extract_event_id(market),
        "question": market.get("question", ""),
        "slug": market.get("slug", ""),
        "end_date": market.get("endDate"),
        "volume_24h": float(market.get("volume24hr") or 0),
        "liquidity": float(market.get("liquidityNum") or market.get("liquidity") or 0),
        "outcomes": GammaClient.outcome_labels(market),
        "outcome_token_ids": GammaClient.outcome_token_ids(market),
        "outcome_prices": GammaClient.outcome_prices(market),
    }


def _search_markets(query: str, limit: int = 10) -> list[dict]:
    q = query.lower().strip()
    gamma = _gamma_client()
    # Score markets by token overlap with the query — Gamma has no full-text endpoint.
    tokens = {t for t in q.replace("?", "").replace(",", " ").split() if len(t) > 2}
    scored: list[tuple[int, dict]] = []
    for market in gamma.iter_markets(active=True, closed=False, order="volume24hr", page=200):
        text = " ".join(
            str(x or "").lower()
            for x in (market.get("question"), market.get("description"), market.get("slug"))
        )
        overlap = sum(1 for t in tokens if t in text)
        if overlap == 0:
            continue
        scored.append((overlap, market))
        if len(scored) >= 400:
            break
    scored.sort(key=lambda x: (x[0], float(x[1].get("volume24hr") or 0)), reverse=True)
    return [_market_summary(m) for _, m in scored[:limit]]


def _get_market_prices(market_id: str) -> dict:
    gamma = _gamma_client()
    market = gamma.market(market_id)
    token_ids = GammaClient.outcome_token_ids(market)
    labels = GammaClient.outcome_labels(market)
    clob = _clob_client()
    quotes = clob.prices_for_outcomes(token_ids)
    return {
        "market_id": str(market.get("id") or market_id),
        "event_id": _extract_event_id(market),
        "question": market.get("question", ""),
        "end_date": market.get("endDate"),
        "outcomes": [
            {
                "label": labels[i] if i < len(labels) else token_ids[i],
                "token_id": token_ids[i],
                "best_ask": quotes[token_ids[i]]["ask"],
                "best_bid": quotes[token_ids[i]]["bid"],
                "mid": quotes[token_ids[i]]["mid"],
            }
            for i in range(len(token_ids))
        ],
    }


def _check_arbitrage(market_id: str) -> dict | None:
    gamma = _gamma_client()
    clob = _clob_client()
    market = gamma.market(market_id)
    opp = _check_market(market, clob, fee_bps=200)
    return opp.as_row() if opp else None


def _size_with_kelly(
    p_true: float,
    p_market: float,
    outcome_label: str = "",
    event_id: str | None = None,
) -> dict:
    result = kelly_size(p_true, p_market)
    portfolio = adjust_for_portfolio(result.dollar_size, event_id=event_id)
    final_dollar = portfolio.adjusted_dollar
    final_shares = (final_dollar / p_market) if p_market > 0 else 0.0
    return {
        "outcome_label": outcome_label,
        "event_id": event_id,
        "edge": result.edge,
        "edge_bps": round(result.edge * 10_000, 1),
        "full_kelly_fraction": result.full_kelly_fraction,
        "scaled_fraction": result.scaled_fraction,
        "dollar_size": round(final_dollar, 2),
        "shares": round(final_shares, 2),
        "kelly_dollar_before_portfolio": round(result.dollar_size, 2),
        "portfolio_reasoning": portfolio.reasoning,
        "cluster_existing_dollar": round(portfolio.cluster_existing_dollar, 2),
        "cluster_size_after": portfolio.cluster_size_after,
        "expected_value_per_share": result.expected_value,
        "reasoning": result.reasoning,
        "bankroll": settings.bankroll,
        "kelly_multiplier": settings.kelly_fraction,
        "max_position_pct": settings.max_position_pct,
    }


def _emit_signal(event_id: str | None = None, **kwargs) -> dict:
    # Encode event_id into news_refs as 'event:<id>' for portfolio clustering
    # without breaking the existing Signal schema.
    refs = list(kwargs.get("news_refs") or [])
    if event_id and not any(r.startswith("event:") for r in refs):
        refs.insert(0, f"event:{event_id}")
    kwargs["news_refs"] = refs
    sig = Signal(**kwargs)
    signal_store.append(sig)
    return {"status": "recorded", "signal": asdict(sig)}


_HANDLERS = {
    "search_markets": _search_markets,
    "get_market_prices": _get_market_prices,
    "check_arbitrage": _check_arbitrage,
    "size_with_kelly": _size_with_kelly,
    "emit_signal": _emit_signal,
}


def run_tool(name: str, payload: dict) -> str:
    """Execute a tool and return a JSON-string result for the model."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        result = handler(**payload)
    except TypeError as e:
        return json.dumps({"error": f"bad arguments: {e}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
    return json.dumps(result, default=str)
