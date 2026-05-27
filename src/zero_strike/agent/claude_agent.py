"""Claude agent that maps live news to Polymarket markets and produces signals.

The agent runs a tool-use loop:

  system   → role / constraints / how-to-decide / how-to-stop
  user     → a batch of fresh news items
  loop:
    assistant → text + (tool_use)* + maybe stop_reason="end_turn"
    user      → tool_result for each tool_use
  until stop_reason="end_turn".

System prompt and tool list are sent with `cache_control` so multi-batch runs
amortize the prefill cost (Anthropic prompt caching).
"""
from __future__ import annotations

import json
from typing import Iterable

from anthropic import Anthropic

from ..calibration import compute_stats, format_calibration_for_prompt, resolution_store
from ..config import settings
from ..execution.signal import signal_store
from .budget import check_budget, daily_cap_usd, record_usage, spend_store
from .cohort_feed import CohortInput
from .news_feed import NewsItem
from .tools import TOOLS, close_clients, run_tool


SYSTEM_PROMPT = """You are Zero-Strike, an information-asymmetry trading agent operating on Polymarket.

Your job is to convert fresh news into pricing dislocations and emit sized trade signals.

How you think:

1. For each news item, ask: does this materially change the probability of any open Polymarket market?
   - "Materially" means at least 200 bps (2 percentage points) vs the current market price.
   - If the answer is no, skip it. Most news is noise. Be ruthless.

2. If yes, use `search_markets` to find the candidate market(s). The market may be phrased
   differently than the headline — think laterally (e.g. "Will X happen by Y date?" maps to
   any news that shifts the prior).

3. Call `get_market_prices` to pull live best bid/ask — do NOT trust the prices in
   search_markets, they can be seconds stale.

4. Call `check_arbitrage` on the same market. If sum-of-asks < 1 or sum-of-bids > 1
   it's a risk-free arb regardless of the news — emit a signal for that immediately.

5. Form a subjective probability `p_true` for the outcome. Be honest about uncertainty:
   if your confidence interval is wider than the market spread, that's a "no bet" — say so
   in plain text and move on. Do not anchor to the market price.

6. Before sizing, call `simulate_fill(token_id, dollar_target=<rough first guess>, side)`
   to get the realistic VWAP. Best-ask is a lie for bets that walk the book. Use the
   returned `vwap` (not the best price) as `p_market` in the next step. If `fully_filled`
   is false, the book is too thin — size down or skip.

7. Call `size_with_kelly(p_true, p_market=<vwap from simulate_fill>, event_id=<from market data>)`.
   ALWAYS pass `event_id` when known — it's the Polymarket event the market belongs to,
   and the sizing function uses it to haircut bets correlated with already-open positions
   in the same event (e.g. multiple election markets that move together). If `dollar_size`
   is 0, the edge is below threshold or cluster cap is full — no bet. If the new
   `dollar_size` differs materially from your `simulate_fill` target, call simulate_fill
   again with the actual size to verify VWAP doesn't shift — then re-Kelly if it does.

8. If sized > 0, call `emit_signal` with full rationale, `event_id`, and the URLs of
   the news items that drove it. Reference at least one specific concrete fact from
   the news — generic reasoning is not signal.

Constraints:

- One signal per market per run. If multiple news items point at the same market, combine them.
- Never emit a signal without first calling get_market_prices on that exact market.
- If `p_true` is within 200 bps of `p_market`, the edge is too thin — pass.
- If a market closes in less than 24 hours and your edge is < 500 bps, pass.
  (Settlement risk / liquidity decay eats thin late edges.)
- Output is signals via emit_signal, plus a brief end-of-turn summary in text.

Stop when you have processed every news item that warranted action. Then end the turn."""


def _build_system_prompt() -> str:
    """Base prompt + (when we have ≥10 resolved signals) the agent's own track record."""
    stats = compute_stats(signal_store.read(), resolution_store.latest_by_signal())
    suffix = format_calibration_for_prompt(stats)
    return SYSTEM_PROMPT + suffix


def run_agent_on_news(
    items: Iterable[NewsItem],
    *,
    cohort_inputs: Iterable[CohortInput] | None = None,
    max_steps: int = 40,
    verbose: bool = True,
) -> dict:
    """Run the agent until end_turn or max_steps. Returns a run summary."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot run agent.")

    ok, msg = check_budget()
    if not ok:
        if verbose:
            print(f"[budget] BLOCKED — {msg}")
        return {"stop": "budget_cap", "reason": msg, "tool_calls": 0, "steps": 0}

    client = Anthropic(api_key=settings.anthropic_api_key)
    system_prompt = _build_system_prompt()

    news_block = "\n\n".join(f"- {it.as_text()}" for it in items)
    cohort_block = "\n\n".join(c.text for c in (cohort_inputs or []))
    if not news_block.strip() and not cohort_block.strip():
        return {"stop": "no_input", "tool_calls": 0, "messages": 0}

    sections = []
    if news_block.strip():
        sections.append("## News\n\n" + news_block)
    if cohort_block.strip():
        sections.append(
            "## Cohort activity (recent fills from our 7 repeatable-edge wallets — treat "
            "these as smart-money tells, not absolute truth)\n\n" + cohort_block
        )
    user_intro = (
        "Here are the freshest inputs. Process them under your operating rules and "
        "emit signals for anything actionable. Be concise in your reasoning text — the "
        "tool calls and emit_signal payloads are the real output.\n\n"
        + "\n\n".join(sections)
    )

    messages: list[dict] = [{"role": "user", "content": user_intro}]
    tool_calls = 0
    steps = 0
    run_cost = 0.0
    budget_aborted = False

    while steps < max_steps:
        steps += 1
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=[{**t, "cache_control": {"type": "ephemeral"}} if i == len(TOOLS) - 1 else t
                   for i, t in enumerate(TOOLS)],
            messages=messages,
        )
        rec = record_usage(settings.anthropic_model, resp.usage)
        run_cost += rec.cost_usd

        assistant_blocks: list[dict] = []
        tool_results: list[dict] = []
        for block in resp.content:
            if block.type == "text":
                assistant_blocks.append({"type": "text", "text": block.text})
                if verbose and block.text.strip():
                    print(f"[agent] {block.text.strip()}")
            elif block.type == "tool_use":
                assistant_blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    }
                )
                tool_calls += 1
                if verbose:
                    preview = json.dumps(block.input)[:160]
                    print(f"[tool] {block.name}({preview})")
                result_text = run_tool(block.name, block.input or {})
                if verbose:
                    print(f"[result] {result_text[:240]}{'…' if len(result_text) > 240 else ''}")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    }
                )

        messages.append({"role": "assistant", "content": assistant_blocks})

        if resp.stop_reason == "end_turn":
            break
        if not tool_results:
            break

        # Mid-loop budget check — if we crossed the cap, stop after recording
        # the last assistant turn but before another expensive prefill.
        ok, msg = check_budget()
        if not ok:
            if verbose:
                print(f"[budget] mid-loop abort — {msg}")
            budget_aborted = True
            break

        messages.append({"role": "user", "content": tool_results})

    close_clients()
    return {
        "stop": "budget_cap" if budget_aborted else resp.stop_reason,
        "tool_calls": tool_calls,
        "steps": steps,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
        "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
        "run_cost_usd": round(run_cost, 4),
        "daily_spend_after": round(spend_store.spend_today(), 4),
        "daily_cap_usd": daily_cap_usd(),
    }
