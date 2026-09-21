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
