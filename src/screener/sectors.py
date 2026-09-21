from __future__ import annotations

from collections import defaultdict

import pandas as pd

# Approximate trading days per calendar window.
LOOKBACK_TRADING_DAYS = {"1m": 21, "3m": 63, "6m": 126}


def pct_return(closes: pd.Series, trading_days: int) -> float:
    """Percentage return over the trailing window.

    Returns 0.0 when history is too short, so a newly listed ETF neither
    helps nor hurts its sector's score.
    """
    if len(closes) <= trading_days:
        return 0.0
    start = closes.iloc[-(trading_days + 1)]
    end = closes.iloc[-1]
    if start == 0:
        return 0.0
    return float((end - start) / start * 100.0)


def rank_sectors(
    etf_closes: dict[str, pd.Series],
    benchmark_closes: pd.Series,
    weights: dict[str, float],
) -> list[tuple[str, float]]:
    """Rank sectors by weighted return relative to the benchmark, descending."""
    scores: list[tuple[str, float]] = []
    for sector, closes in etf_closes.items():
        score = 0.0
        for window, weight in weights.items():
            days = LOOKBACK_TRADING_DAYS[window]
            relative = pct_return(closes, days) - pct_return(benchmark_closes, days)
            score += weight * relative
        scores.append((sector, score))
    return sorted(scores, key=lambda pair: pair[1], reverse=True)


def sector_breadth(
    sector_of: dict[str, str], above_cloud: dict[str, bool]
) -> dict[str, float]:
    """Percentage of each sector's symbols currently above their own cloud.

    Symbols with no reading (insufficient history) are excluded entirely
    rather than counted as False, which would understate breadth.
    """
    totals: dict[str, int] = defaultdict(int)
    above: dict[str, int] = defaultdict(int)
    for symbol, is_above in above_cloud.items():
        sector = sector_of.get(symbol)
        if sector is None:
            continue
        totals[sector] += 1
        if is_above:
            above[sector] += 1
    return {s: above[s] / totals[s] * 100.0 for s in totals}
