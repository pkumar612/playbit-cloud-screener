import numpy as np
import pandas as pd

from screener.config import load_config
from screener.main import MIN_CONVERGENCE_MULTIPLE, evaluate_symbol


def _daily(n, pattern):
    """Build n daily bars whose closes follow `pattern` (a callable of index)."""
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    closes = np.array([pattern(i) for i in range(n)], dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(n, 1_000_000),
        },
        index=idx,
    )


def test_symbol_with_too_little_history_yields_nothing():
    cfg = load_config("config.yaml")
    bars = _daily(50, lambda i: 100.0)
    assert evaluate_symbol("TINY", "Technology", 5e9, bars, cfg) == []


def test_flat_price_sitting_in_its_own_cloud_yields_no_new_signal():
    """A permanently flat series always touches, so it never has the clear
    run the quiet period requires. It must stay silent."""
    cfg = load_config("config.yaml")
    bars = _daily(900, lambda i: 100.0)
    assert evaluate_symbol("FLAT", "Technology", 5e9, bars, cfg) == []


def test_pullback_into_cloud_after_long_rally_fires():
    cfg = load_config("config.yaml")

    def pattern(i):
        # A long steady rally lifts price far above the cloud, then the final
        # bar gaps down into it. With ema_bot ~499 and ema_top ~504 at the
        # end, a close of 502 gives low=496.98 and high=507.02, which overlaps
        # the band, while the five preceding bars sit near 547 and are clear.
        if i < 899:
            return 100.0 + i * 0.5
        return 502.0

    signals = evaluate_symbol("RALLY", "Technology", 5e9, _daily(900, pattern), cfg)
    assert any(s.timeframe == "daily" for s in signals)
    daily_signal = next(s for s in signals if s.timeframe == "daily")
    assert daily_signal.symbol == "RALLY"
    assert daily_signal.sector == "Technology"
    assert daily_signal.ema_bot <= daily_signal.ema_top


def test_weekly_skipped_when_under_ema_length_bars():
    cfg = load_config("config.yaml")
    bars = _daily(900, lambda i: 100.0 + i * 0.5)
    # 900 business days is ~180 weekly bars, under ema_length, so weekly is
    # skipped entirely rather than reported.
    weekly = [s for s in evaluate_symbol("X", "Technology", 5e9, bars, cfg)
              if s.timeframe == "weekly"]
    assert weekly == []


def test_convergence_multiple_is_three():
    assert MIN_CONVERGENCE_MULTIPLE == 3
