import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from zero_strike.agent.budget import (
    UsageRecord,
    SpendStore,
    check_budget,
    daily_cap_usd,
    price_per_mtok,
    record_usage,
)


def test_price_per_mtok_defaults():
    assert price_per_mtok("claude-opus-4-7") == (15.00, 75.00)
    assert price_per_mtok("claude-sonnet-4-6") == (3.00, 15.00)


def test_price_per_mtok_env_override(monkeypatch):
    monkeypatch.setenv("PRICING_CLAUDE_OPUS_4_7_INPUT_PER_MTOK", "12.5")
    monkeypatch.setenv("PRICING_CLAUDE_OPUS_4_7_OUTPUT_PER_MTOK", "60.0")
    assert price_per_mtok("claude-opus-4-7") == (12.5, 60.0)


def test_usage_record_cost_calculation():
    usage = SimpleNamespace(
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )
    rec = UsageRecord.from_usage("claude-opus-4-7", usage)
    # 1M input * $15/M + 0.5M output * $75/M = $15 + $37.5 = $52.5
    assert abs(rec.cost_usd - 52.5) < 1e-6


def test_usage_record_with_cache_tokens():
    usage = SimpleNamespace(
        input_tokens=100_000,
        output_tokens=10_000,
        cache_read_input_tokens=1_000_000,    # discounted at 10% of input
        cache_creation_input_tokens=200_000,  # premium at 125% of input
    )
    rec = UsageRecord.from_usage("claude-opus-4-7", usage)
    # $1.50 (input) + $0.75 (output) + $1.50 (cache_read 1M*$15*0.1) + $3.75 (cache_write 200K*$15*1.25)
    expected = 1.50 + 0.75 + 1.50 + 3.75
    assert abs(rec.cost_usd - expected) < 1e-6


def test_spend_store_roundtrip(tmp_path: Path):
    store = SpendStore(path=tmp_path / "spend.jsonl")
    usage = SimpleNamespace(
        input_tokens=1_000_000, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    rec = UsageRecord.from_usage("claude-opus-4-7", usage)
    store.append(rec)
    assert len(store.read()) == 1
    assert store.spend_today() > 0  # written for today


def test_check_budget_allows_under_cap(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DAILY_SPEND_CAP_USD", "100")
    store = SpendStore(path=tmp_path / "spend.jsonl")
    monkeypatch.setattr("zero_strike.agent.budget.spend_store", store)
    ok, msg = check_budget()
    assert ok
    assert "0.00" in msg


def test_check_budget_blocks_over_cap(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DAILY_SPEND_CAP_USD", "1.0")
    store = SpendStore(path=tmp_path / "spend.jsonl")
    monkeypatch.setattr("zero_strike.agent.budget.spend_store", store)
    # Burn $2 to push past the $1 cap.
    usage = SimpleNamespace(
        input_tokens=2_000_000, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    # Using sonnet ($3/M input) → $6 of spend
    rec = UsageRecord.from_usage("claude-sonnet-4-6", usage)
    store.append(rec)
    ok, msg = check_budget()
    assert not ok
    assert "cap" in msg.lower()


def test_record_usage_writes_and_returns(monkeypatch, tmp_path: Path):
    store = SpendStore(path=tmp_path / "spend.jsonl")
    monkeypatch.setattr("zero_strike.agent.budget.spend_store", store)
    usage = SimpleNamespace(
        input_tokens=10_000, output_tokens=1_000,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    rec = record_usage("claude-haiku-4-5", usage)
    assert rec.cost_usd > 0
    assert len(store.read()) == 1
