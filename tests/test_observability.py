from zero_strike.observability.metrics import Metric, render_prometheus


def test_render_basic_gauge():
    m = [Metric("zs_x", "gauge", "test", 42.0)]
    out = render_prometheus(m)
    assert "# HELP zs_x test" in out
    assert "# TYPE zs_x gauge" in out
    assert "zs_x 42.0" in out


def test_render_with_labels():
    m = [
        Metric("zs_cluster_exposure_usd", "gauge", "h", 100.0, labels={"event_id": "e1"}),
        Metric("zs_cluster_exposure_usd", "gauge", "h", 200.0, labels={"event_id": "e2"}),
    ]
    out = render_prometheus(m)
    # Header emitted once.
    assert out.count("# HELP zs_cluster_exposure_usd") == 1
    assert 'zs_cluster_exposure_usd{event_id="e1"} 100.0' in out
    assert 'zs_cluster_exposure_usd{event_id="e2"} 200.0' in out


def test_render_escapes_quotes_and_backslashes():
    m = [Metric("zs_x", "gauge", "h", 1.0, labels={"q": 'a"b\\c'})]
    out = render_prometheus(m)
    assert 'q="a\\"b\\\\c"' in out


def test_collect_metrics_works_with_empty_stores(monkeypatch, tmp_path):
    from zero_strike.execution.signal import SignalStore
    from zero_strike.calibration.store import ResolutionStore
    from zero_strike.agent.budget import SpendStore
    import zero_strike.observability.metrics as M

    monkeypatch.setattr(M, "signal_store", SignalStore(path=tmp_path / "s.jsonl"))
    monkeypatch.setattr(M, "resolution_store", ResolutionStore(path=tmp_path / "r.jsonl"))
    monkeypatch.setattr(M, "spend_store", SpendStore(path=tmp_path / "sp.jsonl"))
    metrics = M.collect_metrics()
    names = {m.name for m in metrics}
    assert "zs_signals_total" in names
    assert "zs_spend_today_usd" in names
