from zero_strike.analytics.edge_metrics import _gini, compute_edge
from zero_strike.analytics.trader_selection import select_repeatable_edge


def test_gini_uniform_is_zero():
    assert abs(_gini([1, 1, 1, 1])) < 1e-9


def test_gini_concentrated_is_high():
    assert _gini([0, 0, 0, 100]) > 0.5


def _fill(maker, ts, maker_asset, taker_asset, m_amt, t_amt):
    return {
        "maker": maker,
        "timestamp": str(ts),
        "makerAssetId": maker_asset,
        "takerAssetId": taker_asset,
        "makerAmountFilled": str(int(m_amt * 1e6)),
        "takerAmountFilled": str(int(t_amt * 1e6)),
    }


def test_compute_edge_simple_winner():
    # Buy 100 shares of token "T" at $0.40 → $40 cost.
    # Sell 100 shares of "T" at $0.70 → $70 proceeds. Net +$30.
    fills = [
        _fill("0xabc", 1, "0", "T", 40, 100),
        _fill("0xabc", 2, "T", "0", 100, 70),
    ]
    rep = compute_edge("0xabc", fills)
    assert rep.n_trades == 1
    assert abs(rep.pnl_usd - 30) < 1e-6
    assert rep.win_rate == 1.0


def test_compute_edge_loser():
    fills = [
        _fill("0xabc", 1, "0", "T", 50, 100),
        _fill("0xabc", 2, "T", "0", 100, 30),
    ]
    rep = compute_edge("0xabc", fills)
    assert rep.n_trades == 1
    assert rep.pnl_usd < 0
    assert rep.win_rate == 0.0


def test_select_repeatable_filters_low_volume():
    from zero_strike.analytics.edge_metrics import EdgeReport
    reports = [
        EdgeReport("0x1", n_trades=5, win_rate=0.9, sizing_discipline=0.8,
                   entry_timing=0.5, risk_adjusted_edge=1.0, edge_score=3, pnl_usd=1000, volume_usd=10000),
        EdgeReport("0x2", n_trades=50, win_rate=0.7, sizing_discipline=0.8,
                   entry_timing=0.3, risk_adjusted_edge=0.6, edge_score=2.5, pnl_usd=2000, volume_usd=20000),
        EdgeReport("0x3", n_trades=80, win_rate=0.6, sizing_discipline=0.5,
                   entry_timing=0.2, risk_adjusted_edge=0.5, edge_score=2, pnl_usd=3000, volume_usd=30000),
        EdgeReport("0x4", n_trades=60, win_rate=0.55, sizing_discipline=0.4,
                   entry_timing=0.0, risk_adjusted_edge=0.2, edge_score=1.5, pnl_usd=1500, volume_usd=15000),
    ]
    picks = select_repeatable_edge(reports, n=2, min_trades=25, min_axes_above_zero=2)
    addrs = [r.address for r, _, _ in picks]
    assert "0x1" not in addrs  # filtered for low trade count
    assert len(picks) <= 2
