import pandas as pd
import pytest

from screener.sectors import (
    LOOKBACK_TRADING_DAYS,
    pct_return,
    rank_sectors,
    sector_breadth,
)


def _closes(values):
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values), freq="D"))


def test_pct_return_over_lookback():
    s = _closes([100.0] * 126 + [110.0])
    assert pct_return(s, 1) == pytest.approx(10.0)


def test_pct_return_with_insufficient_history_returns_zero():
    assert pct_return(_closes([100.0, 101.0]), 126) == pytest.approx(0.0)


def test_lookback_windows_are_trading_days():
    assert LOOKBACK_TRADING_DAYS == {"1m": 21, "3m": 63, "6m": 126}


def test_rank_sectors_orders_by_weighted_relative_strength():
    n = 200
    flat = _closes([100.0] * n)
    # Strong sector: +20% over the full window. Weak: -10%.
    strong = _closes([100.0] * (n - 1) + [120.0])
    weak = _closes([100.0] * (n - 1) + [90.0])
    weights = {"1m": 0.3, "3m": 0.4, "6m": 0.3}

    ranked = rank_sectors(
        {"Technology": strong, "Utilities": weak}, flat, weights
    )
    assert [name for name, _ in ranked] == ["Technology", "Utilities"]
    assert ranked[0][1] > 0
    assert ranked[1][1] < 0


def test_rank_sectors_subtracts_benchmark():
    """A sector that rose exactly as much as SPY has zero relative strength."""
    n = 200
    same = _closes([100.0] * (n - 1) + [120.0])
    ranked = rank_sectors({"Technology": same}, same, {"1m": 0.3, "3m": 0.4, "6m": 0.3})
    assert ranked[0][1] == pytest.approx(0.0)


def test_sector_breadth_percentage():
    sector_of = {"AAPL": "Technology", "MSFT": "Technology", "XOM": "Energy"}
    above = {"AAPL": True, "MSFT": False, "XOM": True}
    assert sector_breadth(sector_of, above) == {"Technology": 50.0, "Energy": 100.0}


def test_sector_breadth_ignores_symbols_with_no_reading():
    sector_of = {"AAPL": "Technology", "MSFT": "Technology"}
    above = {"AAPL": True}  # MSFT had insufficient history
    assert sector_breadth(sector_of, above) == {"Technology": 100.0}


def test_sector_breadth_empty_sector_absent():
    assert sector_breadth({"AAPL": "Technology"}, {}) == {}
