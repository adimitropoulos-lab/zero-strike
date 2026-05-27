from pathlib import Path
from unittest.mock import patch

from zero_strike.execution.signal import Signal, SignalStore
from zero_strike.calibration.store import Resolution, ResolutionStore
from zero_strike.sizing.portfolio import (
    adjust_for_portfolio,
    open_exposure_by_event,
    _signal_event_id,
)


def _make_signal(market_id, event_id=None, dollar_size=100.0, created_unix=1):
    refs = [f"event:{event_id}"] if event_id else []
    return Signal(
        market_id=market_id,
        question="Q?",
        outcome="YES",
        side="BUY",
        p_true=0.7,
        p_market=0.4,
        shares=10,
        dollar_size=dollar_size,
        edge_bps=3000,
        rationale="t",
        news_refs=refs,
        created_unix=created_unix,
    )


def test_signal_event_id_extraction():
    s = _make_signal("m1", event_id="evt-42")
    assert _signal_event_id(s) == "evt-42"
    s2 = _make_signal("m1", event_id=None)
    assert _signal_event_id(s2) is None


def test_adjust_no_event_passes_through():
    a = adjust_for_portfolio(500, event_id=None, bankroll=10_000)
    assert a.adjusted_dollar == 500


def test_adjust_first_bet_in_cluster_no_discount(tmp_path: Path, monkeypatch):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    monkeypatch.setattr("zero_strike.sizing.portfolio.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.sizing.portfolio.resolution_store", res_store)

    a = adjust_for_portfolio(500, event_id="evt-1", bankroll=10_000, cluster_cap_pct=0.10)
    # No existing bets in cluster → discount factor 1/(1+0) = 1.0 → no change.
    assert a.adjusted_dollar == 500
    assert a.cluster_size_after == 1


def test_adjust_second_bet_gets_correlation_discount(tmp_path: Path, monkeypatch):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    sig_store.append(_make_signal("m1", event_id="evt-1", dollar_size=400))
    monkeypatch.setattr("zero_strike.sizing.portfolio.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.sizing.portfolio.resolution_store", res_store)

    a = adjust_for_portfolio(500, event_id="evt-1", bankroll=10_000, cluster_cap_pct=0.10, rho=0.5)
    # n=2, rho=0.5 → factor = 1/(1+1*0.5) = 0.667 → 500 * 0.667 = 333.3
    assert abs(a.adjusted_dollar - (500 / 1.5)) < 1e-6
    assert a.cluster_size_after == 2


def test_adjust_hits_hard_cluster_cap(tmp_path: Path, monkeypatch):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    # Existing exposure already at cap.
    sig_store.append(_make_signal("m1", event_id="evt-1", dollar_size=1000))
    monkeypatch.setattr("zero_strike.sizing.portfolio.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.sizing.portfolio.resolution_store", res_store)

    a = adjust_for_portfolio(500, event_id="evt-1", bankroll=10_000, cluster_cap_pct=0.10)
    # Cap is $1000, existing is $1000, room = 0 → adjusted = 0.
    assert a.adjusted_dollar == 0
    assert "cluster_cap" in a.reasoning


def test_resolved_signals_dont_count_toward_open_exposure(tmp_path: Path, monkeypatch):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    s = _make_signal("m1", event_id="evt-1", dollar_size=500, created_unix=100)
    sig_store.append(s)
    res_store.append(Resolution(
        signal_key=f"m1:100", market_id="m1", resolved_unix=200,
        market_closed=True, outcome_indicator=1.0, resolution_price=1.0,
        realized_pnl_usd=50, brier=0.09, hit=True,
    ))
    monkeypatch.setattr("zero_strike.sizing.portfolio.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.sizing.portfolio.resolution_store", res_store)

    exposure = open_exposure_by_event()
    # The only signal is resolved, so the event has zero open exposure.
    assert "evt-1" not in exposure or len(exposure["evt-1"]) == 0
