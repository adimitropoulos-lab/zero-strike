from pathlib import Path

import zero_strike.execution.safety as S


def _reset_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(S, "_LIVE_LOG", tmp_path / "live.jsonl")
    monkeypatch.setattr(S, "_HALT_FLAG", tmp_path / "halt.flag")


def test_precheck_blocks_above_per_order_cap(monkeypatch, tmp_path: Path):
    _reset_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_ORDER_USD", "100")
    ok, reason = S.precheck(500)
    assert not ok
    assert "per-order cap" in reason


def test_precheck_blocks_when_halted(monkeypatch, tmp_path: Path):
    _reset_paths(monkeypatch, tmp_path)
    S.halt("test halt")
    ok, reason = S.precheck(50)
    assert not ok
    assert "halted" in reason
    S.unhalt()
    ok2, _ = S.precheck(50)
    assert ok2


def test_precheck_blocks_above_daily_volume_cap(monkeypatch, tmp_path: Path):
    _reset_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_DAILY_VOLUME_USD", "1000")
    monkeypatch.setenv("MAX_ORDER_USD", "10000")
    # Pre-fill log with $900 of today's submissions.
    import time
    rec = S.LiveOrderRecord(
        ts_unix=int(time.time()),
        date=time.strftime("%Y-%m-%d", time.gmtime()),
        market_id="m", outcome="YES", side="BUY",
        dollar_size=900, shares=1, price=0.5, submitted=True,
    )
    S.record(rec)
    ok, reason = S.precheck(200)  # 900 + 200 = 1100 > 1000
    assert not ok
    assert "daily volume cap" in reason


def test_precheck_passes_under_caps(monkeypatch, tmp_path: Path):
    _reset_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_ORDER_USD", "1000")
    monkeypatch.setenv("MAX_DAILY_VOLUME_USD", "10000")
    monkeypatch.setenv("DAILY_LOSS_CAP_USD", "1000")
    ok, reason = S.precheck(500)
    assert ok
    assert reason == "ok"


def test_record_and_read_roundtrip(monkeypatch, tmp_path: Path):
    _reset_paths(monkeypatch, tmp_path)
    import time
    rec = S.LiveOrderRecord(
        ts_unix=int(time.time()),
        date=time.strftime("%Y-%m-%d", time.gmtime()),
        market_id="m1", outcome="YES", side="BUY",
        dollar_size=100, shares=200, price=0.5, submitted=True,
    )
    S.record(rec)
    rows = S.read_records()
    assert len(rows) == 1
    assert rows[0].market_id == "m1"
