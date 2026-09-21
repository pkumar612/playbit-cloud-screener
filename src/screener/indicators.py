from __future__ import annotations

import pandas as pd

REQUIRED_COLUMNS = ("open", "high", "low", "close")


def ema(series: pd.Series, length: int) -> pd.Series:
    """Exponential moving average matching Pine Script's ta.ema.

    Pine seeds from the first source value:
        ema = alpha * src + (1 - alpha) * nz(ema[1]),  alpha = 2 / (length + 1)

    pandas' ewm(adjust=False) seeds identically. Do NOT substitute an
    SMA-seeded variant; it will not match TradingView.
    """
    return series.ewm(span=length, adjust=False).mean()


def cloud_bounds(bars: pd.DataFrame, length: int) -> pd.DataFrame:
    """Add the PlayBit cloud bounds to an OHLC frame.

    The cloud is the band between EMA(high, length) and EMA(close, length).
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    out = bars.copy()
    out["ema_top"] = ema(out["high"], length)
    out["ema_bot"] = ema(out["close"], length)
    return out


BOUND_COLUMNS = ("ema_top", "ema_bot")


def mark_cloud_state(bars: pd.DataFrame) -> pd.DataFrame:
    """Flag each bar as touching the cloud, or clear of it.

    A bar touches when its range overlaps the band at all, inclusive of the
    boundaries. A bar is clear when it lies entirely outside the band. These
    are exact complements.
    """
    missing = [c for c in BOUND_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")

    out = bars.copy()
    out["touch"] = (out["low"] <= out["ema_top"]) & (out["high"] >= out["ema_bot"])
    out["clear"] = ~out["touch"]
    return out


WEEKLY_AGGREGATION = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
}


def to_weekly(daily_bars: pd.DataFrame) -> pd.DataFrame:
    """Resample daily bars to weekly bars labelled by week-ending Friday.

    Weeks with no trading days are dropped rather than emitted as NaN rows,
    which would otherwise poison the EMA.
    """
    columns = {k: v for k, v in WEEKLY_AGGREGATION.items() if k in daily_bars.columns}
    weekly = daily_bars.resample("W-FRI").agg(columns)
    # Drop weeks with no trading activity (all OHLC are NaN).
    # Under pandas 3.0, dropna(how="all") doesn't work because volume=0, not NaN.
    # So we check if any of the OHLC columns have non-NaN values.
    ohlc_cols = [c for c in ["open", "high", "low", "close"] if c in weekly.columns]
    return weekly[weekly[ohlc_cols].notna().any(axis=1)]
