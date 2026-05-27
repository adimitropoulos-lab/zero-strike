from unittest.mock import MagicMock

from zero_strike.polymarket.clob import ClobClient


def _clob_with_book(asks=None, bids=None):
    c = ClobClient.__new__(ClobClient)
    c.http = MagicMock()
    c.http.get_json.return_value = {"asks": asks or [], "bids": bids or []}
    return c


def test_book_levels_buy_sorted_ascending():
    asks = [
        {"price": "0.55", "size": "100"},
        {"price": "0.50", "size": "50"},   # better, must come first
        {"price": "0.60", "size": "200"},
    ]
    c = _clob_with_book(asks=asks)
    # Override book() to return the test data directly
    c.book = lambda token_id: {"asks": asks, "bids": []}
    levels = c.book_levels("T", "BUY")
    assert [p for p, _ in levels] == [0.50, 0.55, 0.60]


def test_book_levels_sell_sorted_descending():
    bids = [
        {"price": "0.40", "size": "100"},
        {"price": "0.45", "size": "50"},   # better, must come first
        {"price": "0.35", "size": "200"},
    ]
    c = _clob_with_book(bids=bids)
    c.book = lambda token_id: {"asks": [], "bids": bids}
    levels = c.book_levels("T", "SELL")
    assert [p for p, _ in levels] == [0.45, 0.40, 0.35]


def test_simulate_fill_within_first_level_no_slippage():
    asks = [{"price": "0.50", "size": "1000"}]   # 1000 shares × 0.50 = $500 of depth
    c = _clob_with_book(asks=asks)
    c.book = lambda token_id: {"asks": asks, "bids": []}
    sim = c.simulate_fill("T", dollar_target=100, side="BUY")
    assert sim.fully_filled
    assert sim.vwap == 0.50
    assert sim.shares == 200      # $100 / $0.50
    assert sim.slippage_bps == 0.0
    assert sim.levels_consumed == 1


def test_simulate_fill_walks_two_levels_vwap_correct():
    # Level 1: 100 shares at $0.40 = $40 of depth
    # Level 2: 100 shares at $0.50 = $50 of depth
    # Target: $60 → fully consume L1 ($40, 100 shares), then $20 of L2 (40 shares at 0.50)
    # Total: 140 shares for $60 → VWAP = 60/140 = 0.4286
    asks = [
        {"price": "0.40", "size": "100"},
        {"price": "0.50", "size": "100"},
    ]
    c = _clob_with_book(asks=asks)
    c.book = lambda token_id: {"asks": asks, "bids": []}
    sim = c.simulate_fill("T", dollar_target=60, side="BUY")
    assert sim.fully_filled
    assert abs(sim.vwap - (60 / 140)) < 1e-6
    assert abs(sim.shares - 140) < 1e-6
    # Slippage = (0.4286 - 0.40) / 0.40 ≈ +714 bps (raw float, full precision)
    assert abs(sim.slippage_bps - ((60 / 140 - 0.40) / 0.40) * 10_000) < 1e-6
    assert sim.levels_consumed == 2


def test_simulate_fill_insufficient_depth_flagged():
    asks = [{"price": "0.50", "size": "10"}]   # only $5 of depth
    c = _clob_with_book(asks=asks)
    c.book = lambda token_id: {"asks": asks, "bids": []}
    sim = c.simulate_fill("T", dollar_target=100, side="BUY")
    assert not sim.fully_filled
    assert sim.filled_dollar == 5.0
    assert sim.shares == 10


def test_simulate_fill_sell_side_slippage_signed_correctly():
    # Selling: higher VWAP is good for us. Best bid 0.50, next 0.40.
    bids = [
        {"price": "0.50", "size": "100"},   # $50
        {"price": "0.40", "size": "100"},   # $40
    ]
    c = _clob_with_book(bids=bids)
    c.book = lambda token_id: {"asks": [], "bids": bids}
    # Target $70 — eat all of L1 ($50, 100 shares), then 50 of L2 (125 shares at 0.40)
    sim = c.simulate_fill("T", dollar_target=70, side="SELL")
    assert sim.fully_filled
    # VWAP = 70 / 225 = 0.3111. We collected less per share than the top bid.
    # Slippage should be POSITIVE (we "paid up" by accepting a worse price).
    assert sim.slippage_bps is not None and sim.slippage_bps > 0


def test_simulate_fill_empty_book():
    c = _clob_with_book(asks=[], bids=[])
    c.book = lambda token_id: {"asks": [], "bids": []}
    sim = c.simulate_fill("T", dollar_target=100, side="BUY")
    assert not sim.fully_filled
    assert sim.vwap is None
    assert sim.shares == 0
