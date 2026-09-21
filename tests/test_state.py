import json

import pandas as pd

from screener.state import (
    MAX_LOOKBACK_BARS,
    is_new_signal,
    load_state,
    lookback_bars,
    new_signal_index,
    save_state,
)


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
        "alerts": {"AAPL:daily": {"last_alert_date": "2026-09-21"}},
    }
    save_state(path, state)
    assert load_state(path) == state


def test_corrupt_state_loads_as_empty(tmp_path):
    """A crashed run must not be worse than one round of duplicate alerts."""
    path = tmp_path / "alerts.json"
    path.write_text("{ this is not json")
    assert load_state(path) == {"last_run": None, "alerts": {}}


def test_byte_corrupt_state_loads_as_empty(tmp_path):
    """Byte-level corruption (non-UTF-8 files) must also load as empty."""
    path = tmp_path / "alerts.json"
    path.write_bytes(b"\xff\xfe\x00corrupt")
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


def test_new_signal_index_finds_an_entry_behind_the_last_bar():
    """A missed run leaves the entry bar one or more bars back. Scanning only
    the last bar loses it permanently, since the quiet-period precondition
    can never be satisfied again."""
    bars = _bars([False] * 5 + [True, False])
    assert new_signal_index(bars, quiet_bars=5, lookback=1) is None
    assert new_signal_index(bars, quiet_bars=5, lookback=2) == 5


def test_new_signal_index_prefers_the_most_recent_entry():
    """Only one alert date is remembered per symbol and timeframe, so an
    older entry reported alongside a newer one would re-fire next run."""
    bars = _bars([False] * 3 + [True] + [False] * 3 + [True])
    assert new_signal_index(bars, quiet_bars=3, lookback=5) == 7


def test_new_signal_index_is_empty_when_nothing_entered():
    assert new_signal_index(_bars([True] * 8), quiet_bars=3, lookback=5) is None


def test_lookback_is_one_on_a_punctual_run():
    calendar = pd.date_range("2026-09-14", periods=5, freq="B")
    assert lookback_bars(calendar, "2026-09-17T21:30:00+00:00") == 1


def test_lookback_covers_every_missed_session():
    calendar = pd.date_range("2026-09-14", periods=5, freq="B")
    assert lookback_bars(calendar, "2026-09-15T21:30:00+00:00") == 3


def test_lookback_is_capped():
    calendar = pd.date_range("2026-06-01", periods=80, freq="B")
    assert lookback_bars(calendar, "2026-06-02T21:30:00+00:00") == MAX_LOOKBACK_BARS


def test_lookback_on_a_first_run_looks_at_the_last_bar_only():
    """With no prior run there is nothing to catch up on, and scanning back
    would alert on stale touches the user never asked about."""
    calendar = pd.date_range("2026-09-14", periods=5, freq="B")
    assert lookback_bars(calendar, None) == 1


def test_lookback_on_an_unparseable_timestamp_catches_up_fully():
    calendar = pd.date_range("2026-09-14", periods=5, freq="B")
    assert lookback_bars(calendar, "not-a-date") == MAX_LOOKBACK_BARS
