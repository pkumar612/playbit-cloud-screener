import json

import numpy as np
import pandas as pd
import pytest

from screener.config import load_config
from screener.main import MIN_CONVERGENCE_MULTIPLE, evaluate_symbol, run


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


class _BoomError(Exception):
    """A distinctive exception, so the test can't pass on a bare `except`."""


def test_run_sends_failure_and_reraises_on_crash(monkeypatch, tmp_path):
    """A crashed scan must alert AND fail the workflow. Firing the alert
    without re-raising would leave the workflow green, and the user would
    read the resulting silence as "no setups today" -- the worst outcome
    this product can produce."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")

    def _raise(min_market_cap):
        raise _BoomError("universe fetch exploded")

    monkeypatch.setattr("screener.main.fetch_universe", _raise)

    failure_calls = []
    monkeypatch.setattr(
        "screener.main.send_failure",
        lambda reason, token, chat_id: failure_calls.append((reason, token, chat_id)),
    )

    cfg = load_config("config.yaml")
    with pytest.raises(_BoomError):
        run(cfg, client=object(), state_path=tmp_path / "alerts.json")

    assert len(failure_calls) == 1
    reason, token, chat_id = failure_calls[0]
    assert "universe fetch exploded" in reason
    assert token == "test-token"
    assert chat_id == "test-chat"


class _QuietStubClient:
    """Returns synthetic bars for whatever symbols it's asked for, so sector
    ranking has data to work with, while the scanned universe stays empty."""

    def daily_bars(self, symbols, start):
        if not symbols:
            return {}
        idx = pd.date_range("2020-01-01", periods=300, freq="B")
        closes = np.full(len(idx), 100.0)
        frame = pd.DataFrame(
            {
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "volume": np.full(len(idx), 1_000_000),
            },
            index=idx,
        )
        return {symbol: frame.copy() for symbol in symbols}


def test_run_stamps_last_run_even_with_no_signals(monkeypatch, tmp_path):
    """A quiet market (zero new signals) must still write a fresh `last_run`
    and be committed. If this ever became conditional on there being
    signals, a quiet market would produce no commit, and after 60 days
    GitHub silently disables the cron in a public repo -- the alerts would
    just stop, with no error anywhere."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr("screener.main.fetch_universe", lambda min_market_cap: [])

    telegram_calls = []
    monkeypatch.setattr(
        "screener.main.send_telegram",
        lambda text, token, chat_id: telegram_calls.append(text),
    )

    cfg = load_config("config.yaml")
    state_path = tmp_path / "alerts.json"

    exit_code = run(cfg, _QuietStubClient(), state_path=state_path)

    assert exit_code == 0
    assert telegram_calls == []
    saved = json.loads(state_path.read_text())
    assert saved["last_run"] is not None
