import time
from pathlib import Path
from unittest.mock import patch

import pytest

from zero_strike.calibration import compute_stats, format_calibration_for_prompt
from zero_strike.calibration.resolution import _match_outcome_index, resolve_signal, signal_key
from zero_strike.calibration.store import Resolution, ResolutionStore
from zero_strike.execution.signal import Signal


def _sig(market_id="1", outcome="YES", p_true=0.7, p_market=0.4, shares=100,
         dollar_size=40, side="BUY", created_unix=1000):
    return Signal(
        market_id=market_id, question="Will X?", outcome=outcome, side=side,
        p_true=p_true, p_market=p_market, shares=shares, dollar_size=dollar_size,
        edge_bps=(p_true - p_market) * 10_000, rationale="t",
        created_unix=created_unix,
    )


def test_match_outcome_case_insensitive():
    assert _match_outcome_index(["YES", "NO"], "yes") == 0
    assert _match_outcome_index(["YES", "NO"], "No") == 1
    assert _match_outcome_index(["YES", "NO"], "Maybe") is None


def test_resolve_market_open_returns_unsettled(monkeypatch):
    sig = _sig()
    mock_market = {"closed": False, "outcomes": '["YES","NO"]', "outcomePrices": '["0.5","0.5"]'}
    with patch("zero_strike.calibration.resolution.GammaClient") as G:
        G.return_value.market.return_value = mock_market
        G.outcome_labels = lambda m: ["YES", "NO"]
        G.outcome_prices = lambda m: [0.5, 0.5]
        r = resolve_signal(sig)
    assert r.market_closed is False
    assert r.outcome_indicator is None
    assert r.brier is None


def test_resolve_market_yes_resolves_yes_bet_correctly():
    sig = _sig(p_true=0.7, p_market=0.4, shares=100, dollar_size=40, outcome="YES")
    mock_market = {"closed": True, "outcomes": '["YES","NO"]', "outcomePrices": '["1","0"]'}
    with patch("zero_strike.calibration.resolution.GammaClient") as G:
        G.return_value.market.return_value = mock_market
        G.outcome_labels = lambda m: ["YES", "NO"]
        G.outcome_prices = lambda m: [1.0, 0.0]
        r = resolve_signal(sig)
    assert r.market_closed
    assert r.outcome_indicator == 1.0
    # PnL = 100 shares * $1 settle - $40 cost = $60
    assert r.realized_pnl_usd == pytest.approx(60.0)
    # Brier = (0.7 - 1)^2 = 0.09
    assert r.brier == pytest.approx(0.09)
    assert r.hit is True


def test_resolve_market_no_loses_yes_bet():
    sig = _sig(p_true=0.7, p_market=0.4, shares=100, dollar_size=40, outcome="YES")
    with patch("zero_strike.calibration.resolution.GammaClient") as G:
        G.return_value.market.return_value = {
            "closed": True, "outcomes": '["YES","NO"]', "outcomePrices": '["0","1"]'
        }
        G.outcome_labels = lambda m: ["YES", "NO"]
        G.outcome_prices = lambda m: [0.0, 1.0]
        r = resolve_signal(sig)
    assert r.outcome_indicator == 0.0
    # PnL = 100 * 0 - 40 = -40
    assert r.realized_pnl_usd == pytest.approx(-40.0)
    assert r.brier == pytest.approx(0.49)
    assert r.hit is False


def test_resolution_store_roundtrip_and_latest(tmp_path: Path):
    store = ResolutionStore(path=tmp_path / "r.jsonl")
    r1 = Resolution("k1", "m1", 100, True, 1.0, 1.0, 10.0, 0.09, True, "", written_unix=1)
    r2 = Resolution("k1", "m1", 200, True, 1.0, 1.0, 10.0, 0.09, True, "", written_unix=2)
    store.append(r1)
    store.append(r2)
    latest = store.latest_by_signal()
    assert latest["k1"].written_unix == 2


def test_compute_stats_brier_and_pnl(tmp_path: Path):
    sigs = [
        _sig(market_id="A", created_unix=1, p_true=0.7),
        _sig(market_id="B", created_unix=2, p_true=0.3),
        _sig(market_id="C", created_unix=3, p_true=0.5),
    ]
    res = {
        signal_key(sigs[0]): Resolution("A:1", "A", 0, True, 1.0, 1.0, 60.0, 0.09, True),
        signal_key(sigs[1]): Resolution("B:2", "B", 0, True, 0.0, 0.0, -40.0, 0.09, False),
        # third unresolved
    }
    stats = compute_stats(sigs, res)
    assert stats.n_signals == 3
    assert stats.n_resolved == 2
    assert stats.brier == pytest.approx(0.09)
    assert stats.hit_rate == 0.5
    assert stats.realized_pnl_usd == pytest.approx(20.0)


def test_format_calibration_silent_below_threshold():
    stats = compute_stats([], {})
    assert format_calibration_for_prompt(stats) == ""


def test_format_calibration_shows_overconfident_drift():
    # 10 signals all predicted at 0.9, all actually 0.4 → systematic overconfidence
    sigs = [_sig(market_id=str(i), created_unix=i, p_true=0.9) for i in range(10)]
    res = {
        signal_key(s): Resolution(signal_key(s), s.market_id, 0, True, 0.4, 0.4, -10, 0.25, False)
        for s in sigs
    }
    stats = compute_stats(sigs, res)
    text = format_calibration_for_prompt(stats, min_resolved=5)
    assert "OVERCONFIDENT" in text
    assert "Brier" in text
