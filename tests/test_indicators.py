import pandas as pd
import pytest

from screener.indicators import cloud_bounds, ema, mark_cloud_state


def test_ema_seeds_from_first_value_not_sma():
    """Pine's ta.ema seeds from the first source value. Seeding from an SMA
    of the first N values is a common but wrong approach that will not match
    TradingView."""
    s = pd.Series([10.0, 20.0, 30.0])
    result = ema(s, length=2)
    # alpha = 2/(2+1) = 2/3
    # bar 0: 10 (seed)
    # bar 1: (2/3)*20 + (1/3)*10 = 16.666...
    # bar 2: (2/3)*30 + (1/3)*16.666... = 25.555...
    assert result.iloc[0] == pytest.approx(10.0)
    assert result.iloc[1] == pytest.approx(16.666667, abs=1e-6)
    assert result.iloc[2] == pytest.approx(25.555556, abs=1e-6)


def test_ema_of_constant_series_is_that_constant():
    s = pd.Series([42.0] * 50)
    assert ema(s, length=10).iloc[-1] == pytest.approx(42.0)


def test_cloud_bounds_adds_top_from_high_and_bot_from_close():
    bars = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0],
            "high": [15.0, 16.0, 17.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.0, 11.0, 12.0],
        },
        index=pd.date_range("2026-01-01", periods=3, freq="D"),
    )
    result = cloud_bounds(bars, length=2)
    assert result["ema_top"].iloc[0] == pytest.approx(15.0)  # seeded from high
    assert result["ema_bot"].iloc[0] == pytest.approx(10.0)  # seeded from close
    # top is built from high, bot from close, so top stays above bot here
    assert (result["ema_top"] >= result["ema_bot"]).all()


def test_cloud_bounds_rejects_missing_columns():
    bars = pd.DataFrame({"close": [1.0]}, index=pd.date_range("2026-01-01", periods=1))
    with pytest.raises(ValueError, match="missing required columns"):
        cloud_bounds(bars, length=2)


def _frame(rows):
    """rows: list of (low, high, ema_bot, ema_top)"""
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[0] for r in rows],
            "close": [r[1] for r in rows],
            "ema_bot": [r[2] for r in rows],
            "ema_top": [r[3] for r in rows],
        },
        index=pd.date_range("2026-01-01", periods=len(rows), freq="D"),
    )


def test_touch_and_clear_cover_every_geometry():
    bars = _frame(
        [
            (120.0, 130.0, 100.0, 110.0),  # 0 fully above band
            (70.0, 80.0, 100.0, 110.0),    # 1 fully below band
            (105.0, 130.0, 100.0, 110.0),  # 2 wick down into band from above
            (70.0, 105.0, 100.0, 110.0),   # 3 wick up into band from below
            (70.0, 130.0, 100.0, 110.0),   # 4 engulfs the whole band
            (103.0, 107.0, 100.0, 110.0),  # 5 entirely inside the band
            (110.0, 130.0, 100.0, 110.0),  # 6 low exactly equals ema_top
            (70.0, 100.0, 100.0, 110.0),   # 7 high exactly equals ema_bot
        ]
    )
    result = mark_cloud_state(bars)
    assert result["touch"].tolist() == [
        False, False, True, True, True, True, True, True
    ]
    # clear is the exact complement of touch
    assert (result["clear"] == ~result["touch"]).all()


def test_mark_cloud_state_requires_bounds():
    bars = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
        index=pd.date_range("2026-01-01", periods=1),
    )
    with pytest.raises(ValueError, match="missing required columns"):
        mark_cloud_state(bars)
