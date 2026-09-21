from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

EMPTY_STATE: dict[str, Any] = {"last_run": None, "alerts": {}}


def is_new_signal(bars: pd.DataFrame, quiet_bars: int) -> bool:
    """True when the last bar enters the cloud after a quiet stretch.

    The user's touch definition is deliberately broad, so a stock oscillating
    around its 200 EMA would otherwise alert every bar. Requiring `quiet_bars`
    fully clear bars beforehand keeps the definition but reports only entries.
    """
    if len(bars) < quiet_bars + 1:
        return False
    if not bool(bars["touch"].iloc[-1]):
        return False
    preceding = bars["clear"].iloc[-(quiet_bars + 1) : -1]
    return bool(preceding.all())


def load_state(path: str | Path) -> dict[str, Any]:
    """Load alert state. Any unreadable state is treated as empty: one round
    of duplicate alerts is preferable to a failed run."""
    path = Path(path)
    if not path.exists():
        return dict(EMPTY_STATE, alerts={})
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return dict(EMPTY_STATE, alerts={})
    if not isinstance(loaded, dict) or "alerts" not in loaded:
        return dict(EMPTY_STATE, alerts={})
    return loaded


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
