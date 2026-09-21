from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

EMPTY_STATE: dict[str, Any] = {"last_run": None, "alerts": {}}

# A run may catch up at most a week of missed sessions. Beyond that the entry
# is too stale to trade, and the quiet-period precondition is better rebuilt
# from fresh bars than reconstructed from a long-dead window.
MAX_LOOKBACK_BARS = 5


def _is_entry_bar(bars: pd.DataFrame, position: int, quiet_bars: int) -> bool:
    """True when the bar at `position` enters the cloud after a quiet stretch."""
    if position < quiet_bars:
        return False
    if not bool(bars["touch"].iloc[position]):
        return False
    preceding = bars["clear"].iloc[position - quiet_bars : position]
    return bool(preceding.all())


def new_signal_index(
    bars: pd.DataFrame, quiet_bars: int, lookback: int = 1
) -> int | None:
    """Position of the most recent entry bar within the last `lookback` bars.

    Scanning only the final bar loses a touch forever whenever a run is missed
    or fails: on the next run the entry bar is no longer last, and its
    quiet-period precondition can never be satisfied again. Scanning back over
    the sessions that elapsed since the last successful run recovers it.

    The most recent qualifying bar wins. Reporting an older one as well would
    re-fire it on the following run, since only one alert date is remembered
    per (symbol, timeframe).
    """
    last = len(bars) - 1
    for position in range(last, max(last - lookback, -1), -1):
        if _is_entry_bar(bars, position, quiet_bars):
            return position
    return None


def is_new_signal(bars: pd.DataFrame, quiet_bars: int) -> bool:
    """True when the last bar enters the cloud after a quiet stretch.

    The user's touch definition is deliberately broad, so a stock oscillating
    around its 200 EMA would otherwise alert every bar. Requiring `quiet_bars`
    fully clear bars beforehand keeps the definition but reports only entries.
    """
    return new_signal_index(bars, quiet_bars) is not None


def lookback_bars(
    calendar: pd.DatetimeIndex,
    last_run: str | None,
    cap: int = MAX_LOOKBACK_BARS,
) -> int:
    """How many trailing bars this run must inspect to cover missed sessions.

    `calendar` is a real trading calendar (the benchmark's bar index), so
    holidays and weekends never inflate the count.
    """
    if not last_run:
        return 1
    try:
        cutoff = pd.Timestamp(last_run).date()
    except (ValueError, TypeError):
        return cap
    elapsed = sum(1 for stamp in calendar if stamp.date() > cutoff)
    return max(1, min(elapsed, cap))


def load_state(path: str | Path) -> dict[str, Any]:
    """Load alert state. Any unreadable state is treated as empty: one round
    of duplicate alerts is preferable to a failed run."""
    path = Path(path)
    if not path.exists():
        return dict(EMPTY_STATE, alerts={})
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return dict(EMPTY_STATE, alerts={})
    if not isinstance(loaded, dict) or "alerts" not in loaded:
        return dict(EMPTY_STATE, alerts={})
    return loaded


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
