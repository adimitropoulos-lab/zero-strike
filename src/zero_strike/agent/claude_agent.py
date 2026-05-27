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

6. Call `size_with_kelly(p_true, p_market)`. If `dollar_size` is 0, the edge is below
   threshold — no bet.

7. If sized > 0, call `emit_signal` with full rationale and the URLs of the news items
   that drove it. Reference at least one specific concrete fact from the news — generic
   reasoning is not signal.

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


def run_agent_on_news(items: Iterable[NewsItem], *, max_steps: int = 40, verbose: bool = True) -> dict:
    """Run the agent until end_turn or max_steps. Returns a run summary."""
    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot run agent.")

    client = Anthropic(api_key=settings.anthropic_api_key)
    system_prompt = _build_system_prompt()

    news_block = "\n\n".join(f"- {it.as_text()}" for it in items)
    if not news_block.strip():
        return {"stop": "no_news", "tool_calls": 0, "messages": 0}

    user_intro = (
        "Here are the freshest news items. Process them under your operating rules and "
        "emit signals for anything actionable. Be concise in your reasoning text — the "
        "tool calls and emit_signal payloads are the real output.\n\n"
        f"{news_block}"
    )

    messages: list[dict] = [{"role": "user", "content": user_intro}]
    tool_calls = 0
    steps = 0

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
        messages.append({"role": "user", "content": tool_results})

    close_clients()
    return {
        "stop": resp.stop_reason,
        "tool_calls": tool_calls,
        "steps": steps,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0),
        "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0),
    }
