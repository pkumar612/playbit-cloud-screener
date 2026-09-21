import pytest
from screener.config import load_config


def test_loads_defaults_from_real_config():
    cfg = load_config("config.yaml")
    assert cfg.min_market_cap == 2_000_000_000
    assert cfg.top_n_sectors == 4
    assert cfg.ema_length == 200
    assert cfg.quiet_bars == {"daily": 5, "weekly": 3}
    assert cfg.timeframes == ["daily", "weekly"]
    assert cfg.benchmark == "SPY"
    assert cfg.sector_etfs["Technology"] == "XLK"
    assert len(cfg.sector_etfs) == 11


def test_rs_weights_must_sum_to_one(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        'min_market_cap: 1\ntop_n_sectors: 1\nema_length: 200\n'
        'quiet_bars: {daily: 5, weekly: 3}\n'
        'rs_weights: {"1m": 0.9, "3m": 0.9, "6m": 0.9}\n'
        'timeframes: [daily]\nbenchmark: SPY\nsector_etfs: {Technology: XLK}\n'
    )
    with pytest.raises(ValueError, match="rs_weights must sum to 1.0"):
        load_config(bad)


def test_rejects_monthly_timeframe(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        'min_market_cap: 1\ntop_n_sectors: 1\nema_length: 200\n'
        'quiet_bars: {daily: 5, weekly: 3}\n'
        'rs_weights: {"1m": 0.3, "3m": 0.4, "6m": 0.3}\n'
        'timeframes: [daily, monthly]\nbenchmark: SPY\nsector_etfs: {Technology: XLK}\n'
    )
    with pytest.raises(ValueError, match="unsupported timeframe"):
        load_config(bad)
