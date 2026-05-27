from unittest.mock import MagicMock

from zero_strike.arb.scanner import _check_market


def _market(token_ids, labels, vol=10_000, mid=None):
    import json
    return {
        "id": "1",
        "question": "Will X happen?",
        "clobTokenIds": json.dumps(token_ids),
        "outcomes": json.dumps(labels),
        "volume24hr": vol,
        "endDate": "2026-12-31T00:00:00Z",
    }


def _clob_with(prices):
    """prices = {token_id: {'ask': float, 'bid': float, 'mid': float}}"""
    clob = MagicMock()
    clob.prices_for_outcomes.return_value = prices
    return clob


def test_buy_side_arb_detected():
    tokens = ["A", "B"]
    clob = _clob_with({
        "A": {"ask": 0.40, "bid": 0.38, "mid": 0.39},
        "B": {"ask": 0.55, "bid": 0.53, "mid": 0.54},
    })
    opp = _check_market(_market(tokens, ["YES", "NO"]), clob, fee_bps=200)
    assert opp is not None
    assert opp.direction == "buy_all"
    assert abs(opp.sum_price - 0.95) < 1e-9
    # profit/$ = (1 - 0.02)/0.95 - 1 ≈ 0.0316
    assert 0.03 < opp.profit_per_dollar < 0.04


def test_sell_side_arb_detected():
    tokens = ["A", "B"]
    clob = _clob_with({
        "A": {"ask": 0.70, "bid": 0.65, "mid": 0.67},
        "B": {"ask": 0.45, "bid": 0.42, "mid": 0.44},
    })
    opp = _check_market(_market(tokens, ["YES", "NO"]), clob, fee_bps=200)
    assert opp is not None
    assert opp.direction == "sell_all"
    # sum bid = 1.07 > 1 + 0.02; profit = 1.07 / 1.02 - 1 ≈ 0.049
    assert opp.profit_per_dollar > 0


def test_no_arb_when_within_fee_band():
    tokens = ["A", "B"]
    clob = _clob_with({
        "A": {"ask": 0.50, "bid": 0.49, "mid": 0.495},
        "B": {"ask": 0.50, "bid": 0.49, "mid": 0.495},
    })
    assert _check_market(_market(tokens, ["YES", "NO"]), clob, fee_bps=200) is None


def test_missing_quote_returns_none():
    clob = _clob_with({
        "A": {"ask": None, "bid": 0.4, "mid": 0.4},
        "B": {"ask": 0.5, "bid": 0.5, "mid": 0.5},
    })
    assert _check_market(_market(["A", "B"], ["YES", "NO"]), clob, fee_bps=200) is None


def test_three_way_market_arb():
    tokens = ["A", "B", "C"]
    clob = _clob_with({
        "A": {"ask": 0.30, "bid": 0.28, "mid": 0.29},
        "B": {"ask": 0.30, "bid": 0.28, "mid": 0.29},
        "C": {"ask": 0.30, "bid": 0.28, "mid": 0.29},
    })
    opp = _check_market(_market(tokens, ["X", "Y", "Z"]), clob, fee_bps=200)
    assert opp is not None and opp.direction == "buy_all"
    assert abs(opp.sum_price - 0.90) < 1e-9
