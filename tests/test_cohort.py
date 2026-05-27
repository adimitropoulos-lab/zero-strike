from pathlib import Path
from unittest.mock import MagicMock, patch

from zero_strike.analytics.cohort_activity import _classify_fill, poll_new_activity
from zero_strike.analytics.cohort_store import CohortMember, CohortSnapshot, CohortStore


def _make_fill(maker, ts, maker_asset, taker_asset, m_amt, t_amt):
    return {
        "maker": maker, "timestamp": str(ts),
        "makerAssetId": maker_asset, "takerAssetId": taker_asset,
        "makerAmountFilled": str(int(m_amt * 1e6)),
        "takerAmountFilled": str(int(t_amt * 1e6)),
    }


def test_classify_buy_fill():
    f = _classify_fill(_make_fill("0xabc", 100, "0", "T1", 40, 100), "0xabc")
    assert f is not None
    assert f.side == "BUY"
    assert f.token_id == "T1"
    assert f.share_amount == 100
    assert f.usdc_amount == 40
    assert f.avg_price == 0.4


def test_classify_sell_fill():
    f = _classify_fill(_make_fill("0xabc", 200, "T1", "0", 50, 30), "0xabc")
    assert f is not None
    assert f.side == "SELL"
    assert f.token_id == "T1"
    assert f.share_amount == 50
    assert f.usdc_amount == 30
    assert f.avg_price == 0.6


def test_classify_rejects_non_member():
    f = _classify_fill(_make_fill("0xdef", 1, "0", "T", 1, 1), "0xabc")
    assert f is None


def test_cohort_store_roundtrip(tmp_path: Path):
    store = CohortStore(path=tmp_path / "c.json", last_seen_path=tmp_path / "ls.json")
    snap = CohortSnapshot(
        selected_unix=1700_000_000, lookback_days=90,
        members=[CohortMember("0x1", 2.5, 0.7, 50, 1500)],
    )
    store.save(snap)
    loaded = store.load()
    assert loaded.members[0].address == "0x1"
    assert loaded.lookback_days == 90
    assert store.addresses() == ["0x1"]


def test_poll_new_activity_uses_last_seen(monkeypatch, tmp_path: Path):
    store = CohortStore(path=tmp_path / "c.json", last_seen_path=tmp_path / "ls.json")
    store.save(CohortSnapshot(
        selected_unix=100, lookback_days=30,
        members=[CohortMember("0xabc", 1.0, 0.6, 30, 100)],
    ))
    monkeypatch.setattr("zero_strike.analytics.cohort_activity.cohort_store", store)

    # Subgraph returns one $500 buy at ts=200.
    fake_fills = [_make_fill("0xabc", 200, "0", "T", 500, 1000)]
    sg = MagicMock()
    sg.trader_fills.return_value = fake_fills
    with patch("zero_strike.analytics.cohort_activity.SubgraphClient") as SG:
        SG.return_value.__enter__.return_value = sg
        first = poll_new_activity(lookback_seconds=3600, min_usdc=100)
    assert len(first) == 1
    assert first[0].side == "BUY"
    # Watermark advanced.
    assert store.get_last_seen()["0xabc"] >= 200


def test_poll_skips_below_min_usdc(monkeypatch, tmp_path: Path):
    store = CohortStore(path=tmp_path / "c.json", last_seen_path=tmp_path / "ls.json")
    store.save(CohortSnapshot(
        selected_unix=100, lookback_days=30,
        members=[CohortMember("0xabc", 1.0, 0.6, 30, 100)],
    ))
    monkeypatch.setattr("zero_strike.analytics.cohort_activity.cohort_store", store)

    tiny = [_make_fill("0xabc", 200, "0", "T", 50, 100)]  # only $50
    sg = MagicMock()
    sg.trader_fills.return_value = tiny
    with patch("zero_strike.analytics.cohort_activity.SubgraphClient") as SG:
        SG.return_value.__enter__.return_value = sg
        out = poll_new_activity(min_usdc=250)
    assert out == []
