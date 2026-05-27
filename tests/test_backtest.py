from pathlib import Path

import pytest

from zero_strike.backtest.replay import _max_drawdown, _sharpe, replay
from zero_strike.backtest.sweep import sweep_parameters
from zero_strike.calibration.store import Resolution, ResolutionStore
from zero_strike.execution.signal import Signal, SignalStore


def test_sharpe_zero_variance_returns_none():
    assert _sharpe([5.0, 5.0, 5.0]) is None


def test_sharpe_positive_stream():
    s = _sharpe([10.0, 20.0, 30.0])
    assert s is not None and s > 0


def test_max_drawdown_classic_curve():
    # Equity walks: +100, +50 (peak=150), -100 (trough=50), +80 (=130)
    # Peak-to-trough drawdown = 150 - 50 = 100.
    assert _max_drawdown([100, 50, -100, 80]) == 100


def test_replay_with_no_signals(monkeypatch, tmp_path: Path):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    monkeypatch.setattr("zero_strike.backtest.replay.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.backtest.replay.resolution_store", res_store)
    r = replay()
    assert r.n_signals == 0
    assert r.n_resolved == 0
    assert r.total_pnl_usd == 0
    assert r.sharpe is None


def test_replay_winning_signal(monkeypatch, tmp_path: Path):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    sig = Signal(
        market_id="A", question="Q?", outcome="YES", side="BUY",
        p_true=0.7, p_market=0.4, shares=100, dollar_size=40,
        edge_bps=3000, rationale="t", created_unix=100,
    )
    sig_store.append(sig)
    res_store.append(Resolution(
        signal_key="A:100", market_id="A", resolved_unix=200,
        market_closed=True, outcome_indicator=1.0, resolution_price=1.0,
        realized_pnl_usd=60.0, brier=0.09, hit=True,
    ))
    monkeypatch.setattr("zero_strike.backtest.replay.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.backtest.replay.resolution_store", res_store)

    r = replay(bankroll=10_000, kelly_fraction=0.25, max_position_pct=0.05, min_edge_bps=200)
    # Edge = p_true - p_market = 0.3 = 3000 bps, passes threshold.
    # full Kelly = (0.7-0.4)/(1-0.4) = 0.5; scaled = 0.5*0.25 = 0.125, capped at 0.05.
    # dollar_size = $500, shares = 1250. PnL = 1250*1.0 - 500 = $750.
    assert r.n_resolved == 1
    assert r.total_pnl_usd == pytest.approx(750.0)
    assert r.win_rate == 1.0


def test_replay_thin_edge_filtered_at_higher_threshold(monkeypatch, tmp_path: Path):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    # 150 bps edge — passes 100 bps, blocked by 200 bps.
    sig = Signal(
        market_id="A", question="Q?", outcome="YES", side="BUY",
        p_true=0.515, p_market=0.50, shares=10, dollar_size=5,
        edge_bps=150, rationale="t", created_unix=1,
    )
    sig_store.append(sig)
    res_store.append(Resolution(
        signal_key="A:1", market_id="A", resolved_unix=2,
        market_closed=True, outcome_indicator=1.0, resolution_price=1.0,
        realized_pnl_usd=5.0, brier=0.235, hit=True,
    ))
    monkeypatch.setattr("zero_strike.backtest.replay.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.backtest.replay.resolution_store", res_store)

    permissive = replay(min_edge_bps=100, kelly_fraction=0.25)
    strict = replay(min_edge_bps=200, kelly_fraction=0.25)
    assert permissive.n_resolved == 1
    assert strict.n_resolved == 0


def test_sweep_returns_grid(monkeypatch, tmp_path: Path):
    sig_store = SignalStore(path=tmp_path / "s.jsonl")
    res_store = ResolutionStore(path=tmp_path / "r.jsonl")
    monkeypatch.setattr("zero_strike.backtest.replay.signal_store", sig_store)
    monkeypatch.setattr("zero_strike.backtest.replay.resolution_store", res_store)

    sr = sweep_parameters(
        kelly_fractions=[0.25, 0.5],
        max_position_pcts=[0.05],
        min_edge_bps_list=[100, 200],
    )
    assert len(sr.results) == 4
