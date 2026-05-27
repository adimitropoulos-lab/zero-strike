from zero_strike.sizing import kelly_fraction, kelly_size


def test_no_edge_returns_zero():
    assert kelly_fraction(0.5, 0.5) == 0.0
    assert kelly_fraction(0.4, 0.6) == 0.0


def test_full_kelly_formula():
    # p_true=0.7, p_market=0.5 → b=1, f* = (1*0.7 - 0.3)/1 = 0.4
    assert abs(kelly_fraction(0.7, 0.5) - 0.4) < 1e-9


def test_full_kelly_skewed_market():
    # p_true=0.6, p_market=0.4 → f* = (0.6 - 0.4)/(1 - 0.4) = 0.333...
    assert abs(kelly_fraction(0.6, 0.4) - (0.2 / 0.6)) < 1e-9


def test_thin_edge_blocked_by_threshold():
    r = kelly_size(0.51, 0.50, bankroll=10_000, kelly_multiplier=0.5,
                   max_position_pct=0.05, min_edge_bps=200)
    assert r.dollar_size == 0.0
    assert "below threshold" in r.reasoning


def test_sized_clamps_to_max_position_pct():
    # Big edge — full Kelly would be huge; cap at 5%.
    r = kelly_size(0.9, 0.5, bankroll=10_000, kelly_multiplier=1.0,
                   max_position_pct=0.05, min_edge_bps=100)
    assert r.scaled_fraction == 0.05
    assert r.dollar_size == 500.0


def test_fractional_kelly_multiplier():
    r = kelly_size(0.7, 0.5, bankroll=10_000, kelly_multiplier=0.25,
                   max_position_pct=1.0, min_edge_bps=100)
    # Full Kelly = 0.4 → quarter Kelly = 0.1 → $1000
    assert abs(r.scaled_fraction - 0.1) < 1e-9
    assert abs(r.dollar_size - 1_000) < 1e-6
