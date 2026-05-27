from pathlib import Path

from zero_strike.execution.signal import Signal, SignalStore


def test_signal_roundtrip(tmp_path: Path):
    store = SignalStore(path=tmp_path / "sigs.jsonl")
    sig = Signal(
        market_id="123",
        question="Will X?",
        outcome="YES",
        side="BUY",
        p_true=0.6,
        p_market=0.4,
        shares=100,
        dollar_size=40,
        edge_bps=2000,
        rationale="news shifted prior",
        news_refs=["https://x"],
    )
    store.append(sig)
    rows = store.read()
    assert len(rows) == 1
    assert rows[0].market_id == "123"
    assert rows[0].edge_bps == 2000
