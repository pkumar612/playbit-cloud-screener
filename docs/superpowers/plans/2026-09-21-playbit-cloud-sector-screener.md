# PlayBit EMA Cloud Sector Screener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alert the user via Telegram when mid/large-cap US stocks in the top 4 relative-strength sectors make contact with the EMA(high,200)/EMA(close,200) cloud on daily or weekly bars.

**Architecture:** A single scheduled batch job on GitHub Actions. Pure-function modules (`indicators`, `sectors`, `state`) hold all the logic and are tested from fixtures with no network. I/O modules (`universe`, `data`, `notify`) are thin wrappers over HTTP. `main` orchestrates. Alert history persists in a JSON file committed back to the repo.

**Tech Stack:** Python 3.14, pandas, requests, PyYAML, pytest, responses (HTTP stubbing), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-21-playbit-cloud-sector-screener-design.md`

## Global Constraints

- **Python 3.14** in CI. Pin it in the workflow; do not rely on the runner default. 3.14 is chosen because it is the only interpreter available on the development machine, and pandas 2.x has no wheels for it. Local and CI must run the same stack.
- **EMA must use `.ewm(span=N, adjust=False).mean()`.** This matches Pine's `ta.ema`, which seeds from the first source value. Never seed with an SMA — that is the common advice online and it produces values that do not match TradingView.
- **`ema_length` is 200** everywhere. Read it from config; never hardcode 200 in a module.
- **No credentials in tracked files.** The repo is public. All secrets come from environment variables. `.env` is gitignored.
- **Tests never touch the network.** Every HTTP call is stubbed with `responses` or fed from a saved fixture.
- **Touch definition is `(low <= emaTop) & (high >= emaBot)`** — any overlap of the bar's range with the band, regardless of close. Do not narrow this to a close-based rule.
- **A bar is "clear" when `high < emaBot or low > emaTop`** — entirely outside the band. "Not touching" and "clear" are the same condition; define it once.
- **Quiet period:** 5 bars daily, 3 bars weekly. Read from config.
- **Sector ETFs:** XLK, XLF, XLI, XLC, XLV, XLY, XLE, XLP, XLB, XLRE, XLU. Benchmark is SPY.
- **RS weights:** 1-month 0.3, 3-month 0.4, 6-month 0.3.
- **Minimum market cap:** `2_000_000_000`.
- **Timeframes:** daily and weekly only. Monthly is explicitly out of scope.

---

## File Structure

| File | Responsibility |
|---|---|
| `config.yaml` | All tunable dials. No logic. |
| `src/screener/config.py` | Loads and validates `config.yaml` into a typed object. |
| `src/screener/indicators.py` | Pure: EMA, cloud bounds, touch/clear, weekly resample. |
| `src/screener/state.py` | Pure: quiet-period state machine. Plus load/save JSON. |
| `src/screener/sectors.py` | Pure: RS ranking, breadth. |
| `src/screener/universe.py` | HTTP: Nasdaq screener -> filtered tickers. |
| `src/screener/data.py` | HTTP: Alpaca bars, chunked and paginated. |
| `src/screener/notify.py` | HTTP: Telegram formatting and send. |
| `src/screener/main.py` | Orchestration only. No business logic. |
| `.github/workflows/scan.yml` | Schedule, secrets, state commit. |

Tasks 1-5 build the pure core and are independently testable with zero credentials. Tasks 6-8 add I/O. Task 9 wires it together. Task 10 ships it.

---

### Task 1: Project scaffold and config loader

**Files:**
- Create: `requirements.txt`, `config.yaml`, `pytest.ini`, `src/screener/__init__.py`, `src/screener/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_config(path: str | Path = "config.yaml") -> Config`, where `Config` is a frozen dataclass with fields `min_market_cap: int`, `top_n_sectors: int`, `ema_length: int`, `quiet_bars: dict[str, int]`, `rs_weights: dict[str, float]`, `timeframes: list[str]`, `sector_etfs: dict[str, str]`, `benchmark: str`.

- [ ] **Step 1: Create the dependency and config files**

`requirements.txt`:
```
pandas==3.0.6
requests==2.32.3
PyYAML==6.0.2
pytest==8.3.4
responses==0.25.3
```

`config.yaml`:
```yaml
min_market_cap: 2000000000
top_n_sectors: 4
ema_length: 200
quiet_bars:
  daily: 5
  weekly: 3
rs_weights:
  "1m": 0.3
  "3m": 0.4
  "6m": 0.3
timeframes:
  - daily
  - weekly
benchmark: SPY
sector_etfs:
  Technology: XLK
  Financial Services: XLF
  Industrials: XLI
  Communication Services: XLC
  Healthcare: XLV
  Consumer Cyclical: XLY
  Energy: XLE
  Consumer Defensive: XLP
  Basic Materials: XLB
  Real Estate: XLRE
  Utilities: XLU
```

`pytest.ini`:
```ini
[pytest]
pythonpath = src
testpaths = tests
```

Create empty `src/screener/__init__.py`.

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:
```python
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
```

Why the third test exists: monthly is out of scope because Alpaca's free tier starts in 2016, giving ~120 monthly bars against the 200 an EMA(200) needs. Silently accepting `monthly` in config would produce garbage rather than an error.

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.config'`

- [ ] **Step 4: Write the implementation**

`src/screener/config.py`:
```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SUPPORTED_TIMEFRAMES = ("daily", "weekly")


@dataclass(frozen=True)
class Config:
    min_market_cap: int
    top_n_sectors: int
    ema_length: int
    quiet_bars: dict[str, int]
    rs_weights: dict[str, float]
    timeframes: list[str]
    benchmark: str
    sector_etfs: dict[str, str]


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())

    weight_total = sum(raw["rs_weights"].values())
    if abs(weight_total - 1.0) > 1e-9:
        raise ValueError(f"rs_weights must sum to 1.0, got {weight_total}")

    for timeframe in raw["timeframes"]:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; "
                f"supported: {', '.join(SUPPORTED_TIMEFRAMES)}"
            )

    return Config(**raw)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add requirements.txt config.yaml pytest.ini src/screener/__init__.py src/screener/config.py tests/test_config.py
git commit -m "feat: add project scaffold and validated config loader"
```

---

### Task 2: EMA and cloud bounds

**Files:**
- Create: `src/screener/indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ema(series: pd.Series, length: int) -> pd.Series`
  - `cloud_bounds(bars: pd.DataFrame, length: int) -> pd.DataFrame` — returns the input frame with `ema_top` and `ema_bot` columns added. Input must have `open`, `high`, `low`, `close` columns and a `DatetimeIndex`.

- [ ] **Step 1: Write the failing test**

`tests/test_indicators.py`:
```python
import pandas as pd
import pytest

from screener.indicators import cloud_bounds, ema


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.indicators'`

- [ ] **Step 3: Write the implementation**

`src/screener/indicators.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/indicators.py tests/test_indicators.py
git commit -m "feat: add Pine-compatible EMA and cloud bounds"
```

---

### Task 3: Touch and clear detection

**Files:**
- Modify: `src/screener/indicators.py`
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Consumes: `cloud_bounds` from Task 2.
- Produces: `mark_cloud_state(bars: pd.DataFrame) -> pd.DataFrame` — adds boolean columns `touch` and `clear` to a frame that already has `ema_top` and `ema_bot`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_indicators.py`:
```python
from screener.indicators import mark_cloud_state


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
```

Note rows 6 and 7: exact boundary contact counts as a touch, because the user chose the broadest definition — any overlap, inclusive.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: FAIL with `ImportError: cannot import name 'mark_cloud_state'`

- [ ] **Step 3: Write the implementation**

Append to `src/screener/indicators.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/indicators.py tests/test_indicators.py
git commit -m "feat: add cloud touch and clear detection"
```

---

### Task 4: Weekly resampling

**Files:**
- Modify: `src/screener/indicators.py`
- Test: `tests/test_indicators.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `to_weekly(daily_bars: pd.DataFrame) -> pd.DataFrame` — resamples daily OHLCV to weekly bars labelled by week-ending Friday.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_indicators.py`:
```python
from screener.indicators import to_weekly


def test_weekly_aggregates_ohlcv_correctly():
    # Mon 2026-01-05 .. Fri 2026-01-09 is one full week
    idx = pd.date_range("2026-01-05", periods=5, freq="D")
    daily = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0, 13.0, 14.0],
            "high": [15.0, 16.0, 20.0, 17.0, 18.0],
            "low": [9.0, 8.0, 11.0, 12.0, 13.0],
            "close": [11.0, 12.0, 13.0, 14.0, 15.0],
            "volume": [100, 200, 300, 400, 500],
        },
        index=idx,
    )
    weekly = to_weekly(daily)
    assert len(weekly) == 1
    assert weekly["open"].iloc[0] == 10.0   # first open of the week
    assert weekly["high"].iloc[0] == 20.0   # max high
    assert weekly["low"].iloc[0] == 8.0     # min low
    assert weekly["close"].iloc[0] == 15.0  # last close
    assert weekly["volume"].iloc[0] == 1500


def test_weekly_handles_holiday_shortened_week():
    """A week missing Monday must still produce exactly one weekly bar."""
    idx = pd.DatetimeIndex(
        ["2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    )
    daily = pd.DataFrame(
        {
            "open": [11.0, 12.0, 13.0, 14.0],
            "high": [16.0, 20.0, 17.0, 18.0],
            "low": [8.0, 11.0, 12.0, 13.0],
            "close": [12.0, 13.0, 14.0, 15.0],
            "volume": [200, 300, 400, 500],
        },
        index=idx,
    )
    weekly = to_weekly(daily)
    assert len(weekly) == 1
    assert weekly["open"].iloc[0] == 11.0
    assert weekly["low"].iloc[0] == 8.0


def test_weekly_drops_empty_periods():
    """A gap of several weeks must not produce all-NaN weekly bars, which
    would corrupt the EMA."""
    idx = pd.DatetimeIndex(["2026-01-05", "2026-02-02"])
    daily = pd.DataFrame(
        {
            "open": [10.0, 20.0],
            "high": [11.0, 21.0],
            "low": [9.0, 19.0],
            "close": [10.5, 20.5],
            "volume": [100, 200],
        },
        index=idx,
    )
    weekly = to_weekly(daily)
    assert len(weekly) == 2
    assert not weekly.isna().any().any()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: FAIL with `ImportError: cannot import name 'to_weekly'`

- [ ] **Step 3: Write the implementation**

Append to `src/screener/indicators.py`:
```python
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
    return weekly.dropna(how="all")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/indicators.py tests/test_indicators.py
git commit -m "feat: add weekly resampling with empty-period handling"
```

---

### Task 5: Quiet-period state machine

**Files:**
- Create: `src/screener/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: frames carrying `touch`/`clear` from Task 3.
- Produces:
  - `is_new_signal(bars: pd.DataFrame, quiet_bars: int) -> bool` — True when the final bar touches and the `quiet_bars` bars before it were all clear.
  - `load_state(path) -> dict`, `save_state(path, state: dict) -> None`
  - State file shape: `{"last_run": "<iso8601>", "alerts": {"<SYMBOL>:<timeframe>": AlertState}}`

- [ ] **Step 1: Write the failing test**

`tests/test_state.py`:
```python
import json

import pandas as pd

from screener.state import is_new_signal, load_state, save_state


def _bars(touch_flags):
    return pd.DataFrame(
        {"touch": touch_flags, "clear": [not t for t in touch_flags]},
        index=pd.date_range("2026-01-01", periods=len(touch_flags), freq="D"),
    )


def test_fires_on_first_touch_after_enough_clear_bars():
    assert is_new_signal(_bars([False] * 5 + [True]), quiet_bars=5) is True


def test_silent_on_repeat_touch():
    # touched yesterday too, so today is not a new entry
    assert is_new_signal(_bars([False] * 5 + [True, True]), quiet_bars=5) is False


def test_silent_when_final_bar_does_not_touch():
    assert is_new_signal(_bars([False] * 5 + [True, False]), quiet_bars=5) is False


def test_boundary_exactly_n_clear_bars_fires():
    assert is_new_signal(_bars([True] + [False] * 5 + [True]), quiet_bars=5) is True


def test_boundary_one_short_of_n_stays_silent():
    assert is_new_signal(_bars([True] + [False] * 4 + [True]), quiet_bars=5) is False


def test_refires_after_leaving_and_returning():
    # touch, then 5 clear, then touch again
    assert is_new_signal(_bars([True, True] + [False] * 5 + [True]), quiet_bars=5) is True


def test_insufficient_history_is_not_a_signal():
    assert is_new_signal(_bars([True]), quiet_bars=5) is False


def test_state_roundtrip(tmp_path):
    path = tmp_path / "alerts.json"
    state = {
        "last_run": "2026-09-21T21:30:00Z",
        "alerts": {"AAPL:daily": {"last_alert_date": "2026-09-21", "last_touch_date": "2026-09-21"}},
    }
    save_state(path, state)
    assert load_state(path) == state


def test_corrupt_state_loads_as_empty(tmp_path):
    """A crashed run must not be worse than one round of duplicate alerts."""
    path = tmp_path / "alerts.json"
    path.write_text("{ this is not json")
    assert load_state(path) == {"last_run": None, "alerts": {}}


def test_missing_state_loads_as_empty(tmp_path):
    assert load_state(tmp_path / "nope.json") == {"last_run": None, "alerts": {}}


def test_save_always_writes_last_run(tmp_path):
    """Public repos disable cron after 60 days without a commit. Every run
    must change the state file so a commit always happens."""
    path = tmp_path / "alerts.json"
    save_state(path, {"last_run": "2026-09-21T21:30:00Z", "alerts": {}})
    written = json.loads(path.read_text())
    assert written["last_run"] == "2026-09-21T21:30:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.state'`

- [ ] **Step 3: Write the implementation**

`src/screener/state.py`:
```python
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
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return dict(EMPTY_STATE, alerts={})
    if not isinstance(loaded, dict) or "alerts" not in loaded:
        return dict(EMPTY_STATE, alerts={})
    return loaded


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_state.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/state.py tests/test_state.py
git commit -m "feat: add quiet-period state machine and state persistence"
```

---

### Task 6: Sector relative strength and breadth

**Files:**
- Create: `src/screener/sectors.py`
- Test: `tests/test_sectors.py`

**Interfaces:**
- Consumes: `Config.rs_weights`, `Config.top_n_sectors` from Task 1.
- Produces:
  - `pct_return(closes: pd.Series, trading_days: int) -> float`
  - `rank_sectors(etf_closes: dict[str, pd.Series], benchmark_closes: pd.Series, weights: dict[str, float]) -> list[tuple[str, float]]` — sorted descending by score. Keys of `etf_closes` are sector names.
  - `sector_breadth(sector_of: dict[str, str], above_cloud: dict[str, bool]) -> dict[str, float]` — percentage 0-100 per sector.
  - Module constant `LOOKBACK_TRADING_DAYS = {"1m": 21, "3m": 63, "6m": 126}`

- [ ] **Step 1: Write the failing test**

`tests/test_sectors.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sectors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.sectors'`

- [ ] **Step 3: Write the implementation**

`src/screener/sectors.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sectors.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/sectors.py tests/test_sectors.py
git commit -m "feat: add sector relative-strength ranking and breadth"
```

---

### Task 7: Universe fetching and filtering

**Files:**
- Create: `src/screener/universe.py`, `tests/fixtures/nasdaq_screener_sample.json`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `Config.min_market_cap` from Task 1.
- Produces:
  - `Ticker` frozen dataclass: `symbol: str`, `sector: str`, `market_cap: float`
  - `fetch_universe(min_market_cap: int, timeout: int = 60) -> list[Ticker]`
  - `parse_universe(payload: dict, min_market_cap: int) -> list[Ticker]`
  - `SECTOR_ALIASES: dict[str, str]` mapping Nasdaq names to the eleven-sector taxonomy.
  - `NASDAQ_SCREENER_URL: str`

- [ ] **Step 1: Create the fixture**

`tests/fixtures/nasdaq_screener_sample.json`:
```json
{
  "data": {
    "rows": [
      {"symbol": "AAPL", "name": "Apple Inc.", "marketCap": "3500000000000.00", "sector": "Technology", "industry": "Computer Manufacturing"},
      {"symbol": "JPM", "name": "JPMorgan Chase", "marketCap": "700000000000.00", "sector": "Finance", "industry": "Major Banks"},
      {"symbol": "TGT", "name": "Target Corp", "marketCap": "60000000000.00", "sector": "Consumer Discretionary", "industry": "Retail"},
      {"symbol": "KO", "name": "Coca-Cola", "marketCap": "280000000000.00", "sector": "Consumer Staples", "industry": "Beverages"},
      {"symbol": "UNH", "name": "UnitedHealth", "marketCap": "500000000000.00", "sector": "Health Care", "industry": "Medical Specialities"},
      {"symbol": "VZ", "name": "Verizon", "marketCap": "170000000000.00", "sector": "Telecommunications", "industry": "Telecom"},
      {"symbol": "TINY", "name": "Tiny Corp", "marketCap": "500000000.00", "sector": "Technology", "industry": "Software"},
      {"symbol": "NOCAP", "name": "No Cap Corp", "marketCap": "", "sector": "Technology", "industry": "Software"},
      {"symbol": "BADCAP", "name": "Bad Cap Corp", "marketCap": "N/A", "sector": "Technology", "industry": "Software"},
      {"symbol": "BRK/A", "name": "Berkshire Class A", "marketCap": "900000000000.00", "sector": "Finance", "industry": "Insurance"},
      {"symbol": "WEIRD", "name": "Unclassified Corp", "marketCap": "5000000000.00", "sector": "Miscellaneous", "industry": "Unknown"},
      {"symbol": "BLANK", "name": "Blank Sector Corp", "marketCap": "5000000000.00", "sector": "", "industry": "Unknown"}
    ]
  }
}
```

- [ ] **Step 2: Write the failing test**

`tests/test_universe.py`:
```python
import json
from pathlib import Path

import pytest
import requests
import responses

from screener.universe import (
    NASDAQ_SCREENER_URL,
    SECTOR_ALIASES,
    fetch_universe,
    parse_universe,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "nasdaq_screener_sample.json").read_text()
)


def _symbols(tickers):
    return sorted(t.symbol for t in tickers)


def test_filters_below_minimum_market_cap():
    tickers = parse_universe(FIXTURE, min_market_cap=2_000_000_000)
    assert "TINY" not in _symbols(tickers)


def test_drops_rows_with_empty_market_cap():
    """An empty marketCap coerces to 0 and is filtered by the cap floor."""
    tickers = parse_universe(FIXTURE, min_market_cap=2_000_000_000)
    assert "NOCAP" not in _symbols(tickers)


def test_drops_rows_with_unparseable_market_cap():
    """A non-numeric marketCap raises inside the try and must be caught.

    This is a different path from an empty string: "N/A" is truthy, so it
    reaches float() and raises ValueError. Without this row the except
    branch has no coverage at all.
    """
    tickers = parse_universe(FIXTURE, min_market_cap=2_000_000_000)
    assert "BADCAP" not in _symbols(tickers)


def test_drops_symbols_with_non_alpha_characters():
    """Share-class symbols like BRK/A are not addressable via Alpaca's bars
    endpoint in this form, so they are excluded rather than silently failing."""
    tickers = parse_universe(FIXTURE, min_market_cap=2_000_000_000)
    assert "BRK/A" not in _symbols(tickers)


def test_drops_unmappable_sectors():
    tickers = parse_universe(FIXTURE, min_market_cap=2_000_000_000)
    assert "WEIRD" not in _symbols(tickers)
    assert "BLANK" not in _symbols(tickers)


def test_normalises_nasdaq_sector_names():
    tickers = {t.symbol: t.sector for t in parse_universe(FIXTURE, 2_000_000_000)}
    assert tickers["JPM"] == "Financial Services"
    assert tickers["TGT"] == "Consumer Cyclical"
    assert tickers["KO"] == "Consumer Defensive"
    assert tickers["UNH"] == "Healthcare"
    assert tickers["VZ"] == "Communication Services"
    assert tickers["AAPL"] == "Technology"


def test_keeps_expected_survivors():
    tickers = parse_universe(FIXTURE, min_market_cap=2_000_000_000)
    assert _symbols(tickers) == ["AAPL", "JPM", "KO", "TGT", "UNH", "VZ"]


def test_market_cap_is_numeric():
    tickers = {t.symbol: t.market_cap for t in parse_universe(FIXTURE, 2_000_000_000)}
    assert tickers["AAPL"] == pytest.approx(3.5e12)


def test_sector_aliases_cover_all_eleven():
    assert len(set(SECTOR_ALIASES.values())) == 11


@responses.activate
def test_fetch_universe_calls_the_endpoint():
    responses.add(responses.GET, NASDAQ_SCREENER_URL, json=FIXTURE, status=200)
    tickers = fetch_universe(min_market_cap=2_000_000_000)
    assert _symbols(tickers) == ["AAPL", "JPM", "KO", "TGT", "UNH", "VZ"]
    assert "User-Agent" in responses.calls[0].request.headers


@responses.activate
def test_fetch_universe_raises_on_http_error():
    responses.add(responses.GET, NASDAQ_SCREENER_URL, status=503)
    with pytest.raises(requests.exceptions.HTTPError):
        fetch_universe(min_market_cap=2_000_000_000)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_universe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.universe'`

- [ ] **Step 4: Write the implementation**

`src/screener/universe.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

import requests

# `download=true` makes the endpoint ignore `limit` and return the full table
# (~2.2 MB, every US-listed stock) in a single response.
NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10&download=true"
)

# The endpoint rejects requests without a browser-like User-Agent.
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; playbit-screener/1.0)"}

# Nasdaq's sector labels differ from the eleven-sector taxonomy the user reads
# on Yahoo Finance. Sectors absent from this map (e.g. "Miscellaneous", "")
# are dropped rather than guessed at.
SECTOR_ALIASES = {
    "Technology": "Technology",
    "Finance": "Financial Services",
    "Industrials": "Industrials",
    "Telecommunications": "Communication Services",
    "Health Care": "Healthcare",
    "Consumer Discretionary": "Consumer Cyclical",
    "Energy": "Energy",
    "Consumer Staples": "Consumer Defensive",
    "Basic Materials": "Basic Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


@dataclass(frozen=True)
class Ticker:
    symbol: str
    sector: str
    market_cap: float


def parse_universe(payload: dict, min_market_cap: int) -> list[Ticker]:
    tickers: list[Ticker] = []
    for row in payload["data"]["rows"]:
        symbol = (row.get("symbol") or "").strip()
        # Alpaca's bars endpoint cannot address share-class symbols in the
        # "BRK/A" form, so exclude anything that is not plain alphabetic.
        if not symbol.isalpha():
            continue

        try:
            market_cap = float(row.get("marketCap") or 0)
        except ValueError:
            continue
        if market_cap < min_market_cap:
            continue

        sector = SECTOR_ALIASES.get((row.get("sector") or "").strip())
        if sector is None:
            continue

        tickers.append(Ticker(symbol=symbol, sector=sector, market_cap=market_cap))
    return tickers


def fetch_universe(min_market_cap: int, timeout: int = 60) -> list[Ticker]:
    response = requests.get(NASDAQ_SCREENER_URL, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return parse_universe(response.json(), min_market_cap)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_universe.py -v`
Expected: 10 passed

- [ ] **Step 6: Commit**

```bash
git add src/screener/universe.py tests/test_universe.py tests/fixtures/nasdaq_screener_sample.json
git commit -m "feat: add universe fetching with cap and sector filtering"
```

---

### Task 8: Alpaca bar fetching

**Files:**
- Create: `src/screener/data.py`
- Test: `tests/test_data.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `AlpacaClient(api_key: str, secret_key: str, chunk_size: int = 200)` with method `daily_bars(symbols: list[str], start: str) -> dict[str, pd.DataFrame]`, returning one OHLCV frame per symbol with a `DatetimeIndex` and columns `open`, `high`, `low`, `close`, `volume`.
  - `AlpacaClient.from_env() -> AlpacaClient` reading `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`.
  - `BARS_URL: str`

- [ ] **Step 1: Write the failing test**

`tests/test_data.py`:
```python
import os

import pytest
import responses

from screener.data import BARS_URL, AlpacaClient


def _bar(t, o, h, l, c, v):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


@responses.activate
def test_returns_one_frame_per_symbol():
    responses.add(
        responses.GET,
        BARS_URL,
        json={
            "bars": {
                "AAPL": [_bar("2026-01-02T05:00:00Z", 1, 2, 0.5, 1.5, 100)],
                "MSFT": [_bar("2026-01-02T05:00:00Z", 3, 4, 2.5, 3.5, 200)],
            },
            "next_page_token": None,
        },
        status=200,
    )
    client = AlpacaClient("key", "secret")
    frames = client.daily_bars(["AAPL", "MSFT"], start="2016-01-01")

    assert set(frames) == {"AAPL", "MSFT"}
    assert list(frames["AAPL"].columns) == ["open", "high", "low", "close", "volume"]
    assert frames["AAPL"]["high"].iloc[0] == 2
    assert str(frames["AAPL"].index[0].date()) == "2026-01-02"


@responses.activate
def test_follows_pagination_and_concatenates():
    responses.add(
        responses.GET,
        BARS_URL,
        json={
            "bars": {"AAPL": [_bar("2026-01-02T05:00:00Z", 1, 2, 0.5, 1.5, 100)]},
            "next_page_token": "PAGE2",
        },
        status=200,
    )
    responses.add(
        responses.GET,
        BARS_URL,
        json={
            "bars": {"AAPL": [_bar("2026-01-05T05:00:00Z", 2, 3, 1.5, 2.5, 150)]},
            "next_page_token": None,
        },
        status=200,
    )
    frames = AlpacaClient("key", "secret").daily_bars(["AAPL"], start="2016-01-01")
    assert len(frames["AAPL"]) == 2
    assert frames["AAPL"].index.is_monotonic_increasing


@responses.activate
def test_chunks_symbols_beyond_chunk_size():
    for _ in range(3):
        responses.add(
            responses.GET,
            BARS_URL,
            json={"bars": {}, "next_page_token": None},
            status=200,
        )
    client = AlpacaClient("key", "secret", chunk_size=2)
    client.daily_bars(["A", "B", "C", "D", "E"], start="2016-01-01")
    assert len(responses.calls) == 3


@responses.activate
def test_sends_credentials_as_headers():
    responses.add(
        responses.GET, BARS_URL, json={"bars": {}, "next_page_token": None}, status=200
    )
    AlpacaClient("mykey", "mysecret").daily_bars(["AAPL"], start="2016-01-01")
    headers = responses.calls[0].request.headers
    assert headers["APCA-API-KEY-ID"] == "mykey"
    assert headers["APCA-API-SECRET-KEY"] == "mysecret"


@responses.activate
def test_symbol_absent_from_response_is_simply_absent():
    """A delisted or unknown symbol must not crash the run."""
    responses.add(
        responses.GET,
        BARS_URL,
        json={"bars": {"AAPL": [_bar("2026-01-02T05:00:00Z", 1, 2, 0.5, 1.5, 100)]},
              "next_page_token": None},
        status=200,
    )
    frames = AlpacaClient("key", "secret").daily_bars(["AAPL", "DEAD"], start="2016-01-01")
    assert "DEAD" not in frames


def test_from_env_requires_both_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
        AlpacaClient.from_env()


def test_from_env_reads_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    client = AlpacaClient.from_env()
    assert client.api_key == "k"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_data.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.data'`

- [ ] **Step 3: Write the implementation**

`src/screener/data.py`:
```python
from __future__ import annotations

import os
import time
from collections import defaultdict

import pandas as pd
import requests

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"

# Alpaca's Basic plan allows 200 requests/minute. Pace below that.
_MIN_SECONDS_BETWEEN_REQUESTS = 0.35
_MAX_RETRIES = 4


class AlpacaClient:
    def __init__(self, api_key: str, secret_key: str, chunk_size: int = 200) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.chunk_size = chunk_size
        self._session = requests.Session()
        self._session.headers.update(
            {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": secret_key,
            }
        )

    @classmethod
    def from_env(cls) -> "AlpacaClient":
        api_key = os.environ.get("ALPACA_API_KEY")
        secret_key = os.environ.get("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise RuntimeError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must both be set"
            )
        return cls(api_key, secret_key)

    def daily_bars(self, symbols: list[str], start: str) -> dict[str, pd.DataFrame]:
        """Fetch daily bars for many symbols, chunked and paginated."""
        collected: dict[str, list[dict]] = defaultdict(list)

        for index in range(0, len(symbols), self.chunk_size):
            chunk = symbols[index : index + self.chunk_size]
            self._fetch_chunk(chunk, start, collected)

        return {
            symbol: _to_frame(rows) for symbol, rows in collected.items() if rows
        }

    def _fetch_chunk(
        self, chunk: list[str], start: str, collected: dict[str, list[dict]]
    ) -> None:
        page_token: str | None = None
        while True:
            params = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": start,
                "limit": 10000,
                "adjustment": "split",
                "feed": "sip",
            }
            if page_token:
                params["page_token"] = page_token

            payload = self._get(params)
            for symbol, rows in (payload.get("bars") or {}).items():
                collected[symbol].extend(rows)

            page_token = payload.get("next_page_token")
            if not page_token:
                return

    def _get(self, params: dict) -> dict:
        for attempt in range(_MAX_RETRIES):
            response = self._session.get(BARS_URL, params=params, timeout=60)
            if response.status_code == 429:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            time.sleep(_MIN_SECONDS_BETWEEN_REQUESTS)
            return response.json()
        raise RuntimeError("Alpaca rate limit not cleared after retries")


def _to_frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["t"] = pd.to_datetime(frame["t"], utc=True).dt.tz_localize(None)
    frame = (
        frame.rename(
            columns={
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
                "t": "timestamp",
            }
        )
        .set_index("timestamp")
        .sort_index()
    )
    return frame[["open", "high", "low", "close", "volume"]]
```

Note on `feed=sip`: the Basic plan serves SIP historical data excluding only the most recent 15 minutes, which is irrelevant to a post-close run. If the account rejects `sip`, switch to `iex`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_data.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/data.py tests/test_data.py
git commit -m "feat: add Alpaca bar client with chunking and pagination"
```

---

### Task 9: Telegram notification

**Files:**
- Create: `src/screener/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `Signal` frozen dataclass: `symbol: str`, `sector: str`, `timeframe: str`, `close: float`, `market_cap: float`, `ema_top: float`, `ema_bot: float`, `low_confidence: bool`
  - `format_message(signals: list[Signal], ranking: list[tuple[str, float]], breadth: dict[str, float], scanned_sectors: list[str], scanned_count: int, skipped_count: int) -> str`
  - `send_telegram(text: str, token: str, chat_id: str) -> None`
  - `send_failure(reason: str, token: str, chat_id: str) -> None`
  - `telegram_credentials_from_env() -> tuple[str, str]`

- [ ] **Step 1: Write the failing test**

`tests/test_notify.py`:
```python
import pytest
import responses

from screener.notify import (
    Signal,
    format_message,
    send_telegram,
    telegram_credentials_from_env,
)

RANKING = [("Technology", 8.4), ("Energy", 3.1), ("Healthcare", -2.0)]
BREADTH = {"Technology": 72.5, "Energy": 41.0, "Healthcare": 30.0}


def _signal(**overrides):
    base = dict(
        symbol="AAPL",
        sector="Technology",
        timeframe="daily",
        close=185.50,
        market_cap=3.5e12,
        ema_top=186.20,
        ema_bot=182.10,
        low_confidence=False,
    )
    base.update(overrides)
    return Signal(**base)


def test_message_contains_symbol_and_timeframe():
    text = format_message([_signal()], RANKING, BREADTH, ["Technology"], 900, 12)
    assert "AAPL" in text
    assert "daily" in text


def test_message_shows_full_ranking_with_breadth():
    text = format_message([_signal()], RANKING, BREADTH, ["Technology"], 900, 12)
    for sector in ("Technology", "Energy", "Healthcare"):
        assert sector in text
    assert "72.5" in text


def test_scanned_sectors_are_marked():
    text = format_message([_signal()], RANKING, BREADTH, ["Technology"], 900, 12)
    tech_line = next(l for l in text.splitlines() if "Technology" in l)
    energy_line = next(l for l in text.splitlines() if "Energy" in l)
    assert "✅" in tech_line
    assert "✅" not in energy_line


def test_low_confidence_signals_are_flagged():
    text = format_message(
        [_signal(timeframe="weekly", low_confidence=True)],
        RANKING, BREADTH, ["Technology"], 900, 12,
    )
    assert "⚠️" in text


def test_footer_reports_counts():
    text = format_message([_signal()], RANKING, BREADTH, ["Technology"], 900, 12)
    assert "900" in text
    assert "12" in text


def test_signals_grouped_by_sector():
    signals = [
        _signal(symbol="AAPL", sector="Technology"),
        _signal(symbol="XOM", sector="Energy"),
        _signal(symbol="MSFT", sector="Technology"),
    ]
    text = format_message(signals, RANKING, BREADTH, ["Technology", "Energy"], 900, 0)
    assert text.index("AAPL") < text.index("XOM")
    assert text.index("MSFT") < text.index("XOM")


@responses.activate
def test_send_telegram_posts_to_the_api():
    responses.add(
        responses.POST,
        "https://api.telegram.org/botTOKEN/sendMessage",
        json={"ok": True},
        status=200,
    )
    send_telegram("hello", token="TOKEN", chat_id="123")
    assert len(responses.calls) == 1


@responses.activate
def test_long_message_is_split_into_multiple_sends():
    """Telegram rejects messages over 4096 characters."""
    responses.add(
        responses.POST,
        "https://api.telegram.org/botTOKEN/sendMessage",
        json={"ok": True},
        status=200,
    )
    send_telegram("x\n" * 5000, token="TOKEN", chat_id="123")
    assert len(responses.calls) > 1


def test_credentials_from_env_requires_both(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        telegram_credentials_from_env()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.notify'`

- [ ] **Step 3: Write the implementation**

`src/screener/notify.py`:
```python
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

TELEGRAM_MAX_CHARS = 4096


@dataclass(frozen=True)
class Signal:
    symbol: str
    sector: str
    timeframe: str
    close: float
    market_cap: float
    ema_top: float
    ema_bot: float
    low_confidence: bool


def _human_cap(market_cap: float) -> str:
    if market_cap >= 1e12:
        return f"${market_cap / 1e12:.1f}T"
    if market_cap >= 1e9:
        return f"${market_cap / 1e9:.1f}B"
    return f"${market_cap / 1e6:.0f}M"


def format_message(
    signals: list[Signal],
    ranking: list[tuple[str, float]],
    breadth: dict[str, float],
    scanned_sectors: list[str],
    scanned_count: int,
    skipped_count: int,
) -> str:
    lines = ["☁️ PlayBit Cloud Touches", "", "Sector strength (✅ = scanned):"]

    for sector, score in ranking:
        mark = "✅" if sector in scanned_sectors else "  "
        pct = breadth.get(sector)
        breadth_text = f"breadth {pct:.1f}%" if pct is not None else "breadth n/a"
        lines.append(f"{mark} {sector}: RS {score:+.2f}, {breadth_text}")

    lines.append("")

    if not signals:
        lines.append("No new touches.")
    else:
        by_sector: dict[str, list[Signal]] = defaultdict(list)
        for signal in signals:
            by_sector[signal.sector].append(signal)

        # Preserve ranking order so the strongest sector appears first.
        ordered = [s for s, _ in ranking if s in by_sector]
        for sector in ordered:
            lines.append(f"— {sector} —")
            for signal in sorted(by_sector[sector], key=lambda s: s.symbol):
                flag = " ⚠️" if signal.low_confidence else ""
                lines.append(
                    f"  {signal.symbol} ({signal.timeframe}){flag} "
                    f"@ {signal.close:.2f} | cap {_human_cap(signal.market_cap)} | "
                    f"cloud {signal.ema_bot:.2f}–{signal.ema_top:.2f}"
                )
            lines.append("")

    lines.append(
        f"Scanned {scanned_count} symbols, skipped {skipped_count} "
        f"· {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    )
    if any(s.low_confidence for s in signals):
        lines.append("⚠️ = EMA not fully converged (limited history)")
    return "\n".join(lines)


def _chunk(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in _chunk(text):
        response = requests.post(
            url, json={"chat_id": chat_id, "text": part}, timeout=30
        )
        response.raise_for_status()


def send_failure(reason: str, token: str, chat_id: str) -> None:
    """A failed run must never be mistaken for a quiet market."""
    send_telegram(f"🚨 Screener run FAILED\n\n{reason}", token, chat_id)


def telegram_credentials_from_env() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set"
        )
    return token, chat_id
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_notify.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/screener/notify.py tests/test_notify.py
git commit -m "feat: add Telegram formatting and delivery"
```

---

### Task 10: Orchestration

**Files:**
- Create: `src/screener/main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: everything from Tasks 1-9.
- Produces:
  - `evaluate_symbol(symbol, sector, market_cap, daily, cfg) -> list[Signal]` — returns the new signals for one symbol across configured timeframes.
  - `run(cfg, client, state_path) -> int` — full pipeline, returns process exit code.
  - `MIN_CONVERGENCE_MULTIPLE = 3`

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'screener.main'`

- [ ] **Step 3: Write the implementation**

`src/screener/main.py`:
```python
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from screener.config import Config, load_config
from screener.data import AlpacaClient
from screener.indicators import cloud_bounds, mark_cloud_state, to_weekly
from screener.notify import (
    Signal,
    format_message,
    send_failure,
    send_telegram,
    telegram_credentials_from_env,
)
from screener.sectors import rank_sectors, sector_breadth
from screener.state import is_new_signal, load_state, save_state
from screener.universe import fetch_universe

# Alpaca free-tier history starts in 2016.
HISTORY_START = "2016-01-01"

# Below this multiple of ema_length, the EMA still carries its seed value.
MIN_CONVERGENCE_MULTIPLE = 3

STATE_PATH = Path("state/alerts.json")


def _prepare(bars: pd.DataFrame, length: int) -> pd.DataFrame:
    return mark_cloud_state(cloud_bounds(bars, length))


def evaluate_symbol(
    symbol: str,
    sector: str,
    market_cap: float,
    daily: pd.DataFrame,
    cfg: Config,
) -> list[Signal]:
    """Return new cloud-entry signals for one symbol across all timeframes."""
    frames: dict[str, pd.DataFrame] = {"daily": daily}
    if "weekly" in cfg.timeframes:
        frames["weekly"] = to_weekly(daily)

    signals: list[Signal] = []
    for timeframe in cfg.timeframes:
        bars = frames.get(timeframe)
        if bars is None or len(bars) < cfg.ema_length:
            continue

        marked = _prepare(bars, cfg.ema_length)
        if not is_new_signal(marked, cfg.quiet_bars[timeframe]):
            continue

        last = marked.iloc[-1]
        signals.append(
            Signal(
                symbol=symbol,
                sector=sector,
                timeframe=timeframe,
                close=float(last["close"]),
                market_cap=market_cap,
                ema_top=float(last["ema_top"]),
                ema_bot=float(last["ema_bot"]),
                low_confidence=len(bars) < cfg.ema_length * MIN_CONVERGENCE_MULTIPLE,
            )
        )
    return signals


def is_above_cloud(daily: pd.DataFrame, length: int) -> bool | None:
    """True/False when computable, None when history is insufficient."""
    if len(daily) < length:
        return None
    marked = _prepare(daily, length)
    last = marked.iloc[-1]
    return bool(last["close"] > last["ema_top"])


def run(cfg: Config, client: AlpacaClient, state_path: Path = STATE_PATH) -> int:
    token, chat_id = telegram_credentials_from_env()

    try:
        universe = fetch_universe(cfg.min_market_cap)

        etf_frames = client.daily_bars(
            list(cfg.sector_etfs.values()) + [cfg.benchmark], start=HISTORY_START
        )
        benchmark = etf_frames[cfg.benchmark]["close"]
        etf_closes = {
            sector: etf_frames[etf]["close"]
            for sector, etf in cfg.sector_etfs.items()
            if etf in etf_frames
        }
        ranking = rank_sectors(etf_closes, benchmark, cfg.rs_weights)
        scanned_sectors = [s for s, _ in ranking[: cfg.top_n_sectors]]

        selected = [t for t in universe if t.sector in scanned_sectors]
        frames = client.daily_bars([t.symbol for t in selected], start=HISTORY_START)

        state = load_state(state_path)
        signals: list[Signal] = []
        above: dict[str, bool] = {}
        skipped = 0

        for ticker in selected:
            daily = frames.get(ticker.symbol)
            if daily is None or daily.empty:
                skipped += 1
                continue

            reading = is_above_cloud(daily, cfg.ema_length)
            if reading is not None:
                above[ticker.symbol] = reading

            for signal in evaluate_symbol(
                ticker.symbol, ticker.sector, ticker.market_cap, daily, cfg
            ):
                key = f"{signal.symbol}:{signal.timeframe}"
                today = str(daily.index[-1].date())
                if state["alerts"].get(key, {}).get("last_alert_date") == today:
                    continue
                state["alerts"][key] = {
                    "last_alert_date": today,
                    "last_touch_date": today,
                }
                signals.append(signal)

        breadth = sector_breadth(
            {t.symbol: t.sector for t in selected}, above
        )

        if signals:
            send_telegram(
                format_message(
                    signals, ranking, breadth, scanned_sectors,
                    len(selected), skipped,
                ),
                token,
                chat_id,
            )

        # Always stamp last_run so the state file changes every weekday and
        # the commit keeps the public repo's cron schedule alive.
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(state_path, state)
        return 0

    except Exception:
        send_failure(traceback.format_exc(limit=3), token, chat_id)
        raise


def main() -> int:
    cfg = load_config("config.yaml")
    return run(cfg, AlpacaClient.from_env())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_main.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: all tests pass, no network access

- [ ] **Step 6: Commit**

```bash
git add src/screener/main.py tests/test_main.py
git commit -m "feat: add screener orchestration"
```

---

### Task 11: TradingView parity check

**Files:**
- Create: `scripts/parity_check.py`, `docs/parity.md`
- Test: manual, run by the user against their own chart

**Interfaces:**
- Consumes: `AlpacaClient`, `cloud_bounds`, `load_config`.
- Produces: a CLI printing computed `ema_top`/`ema_bot` for given symbols.

This task exists because the user will compare alerts against their own TradingView chart. If the numbers do not match, every other test passing is irrelevant.

- [ ] **Step 1: Write the script**

`scripts/parity_check.py`:
```python
"""Print the computed cloud bounds so they can be compared against TradingView.

Usage:
    python scripts/parity_check.py AAPL MSFT NVDA

On the TradingView chart, add the PB-EMA indicator, hover the most recent
candle, and read "PB EMA Top" and "PB EMA Bot" from the status line. They
should match the daily values printed here to within a few cents.
"""

from __future__ import annotations

import sys

from screener.config import load_config
from screener.data import AlpacaClient
from screener.indicators import cloud_bounds, to_weekly
from screener.main import HISTORY_START, MIN_CONVERGENCE_MULTIPLE


def main(symbols: list[str]) -> int:
    cfg = load_config("config.yaml")
    frames = AlpacaClient.from_env().daily_bars(symbols, start=HISTORY_START)

    for symbol in symbols:
        daily = frames.get(symbol)
        if daily is None or len(daily) < cfg.ema_length:
            print(f"{symbol}: insufficient history")
            continue

        print(f"\n{symbol}")
        for label, bars in (("daily", daily), ("weekly", to_weekly(daily))):
            if len(bars) < cfg.ema_length:
                print(f"  {label:6}: insufficient history ({len(bars)} bars)")
                continue
            last = cloud_bounds(bars, cfg.ema_length).iloc[-1]
            warn = (
                "  ⚠️ not converged"
                if len(bars) < cfg.ema_length * MIN_CONVERGENCE_MULTIPLE
                else ""
            )
            print(
                f"  {label:6}: date={bars.index[-1].date()} "
                f"close={last['close']:.2f} "
                f"top={last['ema_top']:.4f} bot={last['ema_bot']:.4f} "
                f"bars={len(bars)}{warn}"
            )
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: Write the comparison record**

`docs/parity.md`:
```markdown
# TradingView Parity

Run `python scripts/parity_check.py AAPL MSFT NVDA JPM XOM` and record the
results against the values read off the TradingView chart.

| Symbol | TF | TV top | Computed top | TV bot | Computed bot | Δ |
|---|---|---|---|---|---|---|
| | | | | | | |

**Expectation.** Daily should match to within a few cents: ~2,500 bars against
a 200-period EMA is 12x the period, so the seed value has fully decayed.

**Weekly is expected to differ** and the difference should be recorded here.
Alpaca's free tier starts in 2016, giving ~520 weekly bars — 2.6x the period,
where the seed still contributes. TradingView seeds from deeper history. If the
recorded difference is material, implement the yfinance deep-history backfill
described in section 13 of the design spec.
```

- [ ] **Step 3: Verify the script runs**

Run: `python scripts/parity_check.py AAPL`
Expected: prints daily bounds; weekly reports insufficient history or a
not-converged warning. Requires Alpaca credentials in the environment.

- [ ] **Step 4: Commit**

```bash
git add scripts/parity_check.py docs/parity.md
git commit -m "feat: add TradingView parity check script"
```

---

### Task 12: GitHub Actions workflow and README

**Files:**
- Create: `.github/workflows/scan.yml`, `.github/workflows/test.yml`, `README.md`, `state/alerts.json`

**Interfaces:**
- Consumes: `src/screener/main.py`.
- Produces: the deployed scheduled job.

- [ ] **Step 1: Seed the state file**

`state/alerts.json`:
```json
{
  "last_run": null,
  "alerts": {}
}
```

- [ ] **Step 2: Write the test workflow**

`.github/workflows/test.yml`:
```yaml
name: tests

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
          cache: pip
      - run: pip install -r requirements.txt
      - run: python -m pytest -v
```

- [ ] **Step 3: Write the scan workflow**

`.github/workflows/scan.yml`:
```yaml
name: scan

on:
  # 21:30 UTC = 5:30pm EDT in summer, 4:30pm EST in winter.
  # After the US close year-round from one cron expression, no DST bug.
  schedule:
    - cron: "30 21 * * 1-5"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: scan
  cancel-in-progress: false

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.14"
          cache: pip

      - run: pip install -r requirements.txt

      - name: Run screener
        env:
          ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
          ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
          PYTHONPATH: src
        run: python -m screener.main

      - name: Commit state
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add state/alerts.json
          # last_run always changes, so this commit should always have content.
          # Public repos disable cron after 60 days with no commits.
          git diff --staged --quiet || git commit -m "chore: update screener state [skip ci]"
          git push
```

`PYTHONPATH: src` is required because the package lives under `src/`.

- [ ] **Step 4: Write the README**

`README.md`:
````markdown
# PlayBit EMA Cloud Sector Screener

Alerts on Telegram when mid/large-cap US stocks in the strongest sectors touch
the PlayBit EMA cloud — the band between `EMA(high, 200)` and `EMA(close, 200)`.

Runs free on GitHub Actions each weekday after the US close.

## Why not TradingView alerts

TradingView's free plan allows no technical alerts at all, and even Premium caps
them at 400 — one per symbol, against a universe of thousands. Pine Screener
needs a paid plan and is a manual screener, not an alert engine. The indicator
is pure EMA arithmetic, so it reproduces exactly outside TradingView.

## Setup

### 1. Fork or clone, then set four secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Where to get it |
|---|---|
| `ALPACA_API_KEY` | alpaca.markets → Paper account → API keys |
| `ALPACA_SECRET_KEY` | shown once, at the same time |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | see below |

**Never commit these.** This repository is public.

### 2. Get your Telegram chat ID

Message your new bot once, then:

```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
```

Read `result[0].message.chat.id` from the response.

### 3. Trigger a manual run

Actions → scan → Run workflow. You should get a Telegram message within a few
minutes.

## Tuning

Everything adjustable lives in `config.yaml`:

| Setting | Default | Effect |
|---|---|---|
| `min_market_cap` | 2e9 | Lower to include small caps |
| `top_n_sectors` | 4 | How many sectors get scanned |
| `quiet_bars.daily` | 5 | Bars a stock must be clear of the cloud before it can alert again |
| `quiet_bars.weekly` | 3 | Same, weekly |
| `rs_weights` | 30/40/30 | Weighting across 1/3/6-month relative strength |

## Verifying against your chart

```bash
PYTHONPATH=src python scripts/parity_check.py AAPL MSFT NVDA
```

Compare against the PB-EMA values in TradingView's status line. Daily should
match to within a few cents. See `docs/parity.md` for why weekly may not.

## Known limitations

- **No monthly timeframe.** Alpaca's free tier starts in 2016, giving ~120
  monthly bars against the 200 an EMA(200) requires.
- **Weekly EMA is not fully converged** (~520 bars vs 200-period). Weekly
  signals carry a ⚠️ marker.
- **Sector labels come from Nasdaq**, which files GOOGL and META under
  Technology where Yahoo Finance uses Communication Services.
- **Cron is best-effort.** GitHub may run the job late. Fine for end-of-day.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -v
```

Tests never hit the network.
````

- [ ] **Step 5: Verify the workflow files parse**

Run: `python -c "import yaml,pathlib; [yaml.safe_load(pathlib.Path(p).read_text()) for p in ['.github/workflows/scan.yml','.github/workflows/test.yml']]; print('workflows parse OK')"`
Expected: `workflows parse OK`

- [ ] **Step 6: Run the full suite one more time**

Run: `python -m pytest -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/scan.yml .github/workflows/test.yml README.md state/alerts.json
git commit -m "feat: add scheduled workflow, CI, and setup documentation"
```

---

## Post-Implementation Checklist

Handled by the user, not the implementing agent:

- [ ] Create the GitHub repo as **public** and push.
- [ ] Add the four secrets.
- [ ] Trigger `scan` manually and confirm a Telegram message arrives.
- [ ] Run the parity check and fill in `docs/parity.md`.
- [ ] If weekly parity is materially off, open the yfinance deep-history backfill
      described in section 13 of the design spec.
