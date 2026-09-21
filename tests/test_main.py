import json

import numpy as np
import pandas as pd
import pytest
import requests

from screener.config import load_config
from screener.main import MIN_CONVERGENCE_MULTIPLE, evaluate_symbol, run
from screener.universe import Ticker


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
    cfg_sectors = load_config("config.yaml").sector_etfs
    universe = [
        Ticker(symbol=f"SYM{i}", sector=sector, market_cap=5e9)
        for i, sector in enumerate(cfg_sectors)
    ]
    monkeypatch.setattr(
        "screener.main.fetch_universe", lambda min_market_cap: universe
    )

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


# --- shared harness for run()-level tests ---------------------------------


def _frame(index, closes):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(len(closes), 1_000_000),
        },
        index=index,
    )


def _bars_ending(periods, pattern, end):
    """Bars on a real business-day calendar ending on `end`."""
    index = pd.bdate_range(end=end, periods=periods)
    return _frame(index, [pattern(i) for i in range(periods)])


class _StubClient:
    """Serves prepared frames by symbol.

    Any symbol without a prepared frame gets a flat frame on `calendar`,
    which is enough for the sector ranking to be computed. Symbols in
    `missing` are omitted from the response entirely, as Alpaca does when it
    has no bars for them.
    """

    def __init__(self, frames=None, calendar=None, missing=()):
        self._frames = frames or {}
        self._calendar = calendar
        self._missing = set(missing)

    def daily_bars(self, symbols, start):
        out = {}
        for symbol in symbols:
            if symbol in self._missing:
                continue
            if symbol in self._frames:
                out[symbol] = self._frames[symbol]
            elif self._calendar is not None:
                out[symbol] = _frame(self._calendar, np.full(len(self._calendar), 100.0))
        return out


def _wire(monkeypatch, universe):
    """Set credentials, stub the universe, and capture both outbound paths."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test-chat")
    monkeypatch.setattr(
        "screener.main.fetch_universe", lambda min_market_cap: list(universe)
    )
    sent: list[str] = []
    monkeypatch.setattr(
        "screener.main.send_telegram",
        lambda text, token, chat_id: sent.append(text),
    )
    failures: list[str] = []
    monkeypatch.setattr(
        "screener.main.send_failure",
        lambda reason, token, chat_id: failures.append(reason),
    )
    return sent, failures


def _rally_then_one_day_dip():
    """900 daily bars: a long rally, a single bar dipping into the cloud on
    the second-to-last session, then back clear above it."""
    periods = 900

    def pattern(i):
        if i < periods - 2:
            return 100.0 + i * 0.5
        if i == periods - 2:
            return 502.0
        return 549.5

    return _bars_ending(periods, pattern, end=pd.Timestamp("2025-06-10"))


# --- C1: a missed run must not lose that day's touches --------------------


def test_touch_from_a_missed_run_is_recovered_on_the_next_run(monkeypatch, tmp_path):
    """The run for day D never happened -- dropped cron, Alpaca 500, Telegram
    429. On D+1 the entry bar is no longer the last bar, and its quiet-period
    precondition can never be satisfied again. Scanning only the last bar
    loses that touch forever, silently. The next run must pick it up."""
    bars = _rally_then_one_day_dip()
    entry_date = str(bars.index[-2].date())

    universe = [Ticker(symbol="RALLY", sector="Technology", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)
    client = _StubClient({"RALLY": bars}, calendar=bars.index)

    state_path = tmp_path / "alerts.json"
    # The last successful run was two sessions ago: D-1. D was missed.
    state_path.write_text(
        json.dumps(
            {
                "last_run": f"{bars.index[-3].date()}T21:30:00+00:00",
                "alerts": {},
            }
        )
    )

    assert run(load_config("config.yaml"), client, state_path=state_path) == 0

    assert failures == []
    assert len(sent) == 1, "the missed touch must still be reported"
    assert "RALLY" in sent[0]
    saved = json.loads(state_path.read_text())
    # Dedup is keyed on the bar's own date, not on today's date.
    assert saved["alerts"]["RALLY:daily"]["last_alert_date"] == entry_date


def test_recovered_touch_is_reported_exactly_once(monkeypatch, tmp_path):
    """Catching up must not turn into re-alerting the same bar every day for
    as long as it stays inside the lookback window."""
    bars = _rally_then_one_day_dip()
    universe = [Ticker(symbol="RALLY", sector="Technology", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)
    client = _StubClient({"RALLY": bars}, calendar=bars.index)

    state_path = tmp_path / "alerts.json"
    stale_run = f"{bars.index[-3].date()}T21:30:00+00:00"
    state_path.write_text(json.dumps({"last_run": stale_run, "alerts": {}}))

    run(load_config("config.yaml"), client, state_path=state_path)
    assert len(sent) == 1

    # Rewind last_run so the second run looks back over the same window and
    # sees the same entry bar again.
    state = json.loads(state_path.read_text())
    state["last_run"] = stale_run
    state_path.write_text(json.dumps(state))

    run(load_config("config.yaml"), client, state_path=state_path)
    assert failures == []
    assert len(sent) == 1, "the same bar must never alert twice"


# --- I1: a weekly bar must alert once, not once per weekday ---------------


def _weekly_entry_bars(end, periods):
    """Daily bars whose *weekly* resample enters the cloud on the final
    (partial) week. The final week's daily prices are flat, so extending the
    frame by another weekday leaves the weekly bar unchanged."""
    dip_start = periods - (end.weekday() + 1)

    def pattern(i):
        return 100.0 + i * 0.5 if i < dip_start else 567.0

    return _bars_ending(periods, pattern, end=end)


def test_weekly_signal_does_not_refire_on_the_next_weekday(monkeypatch, tmp_path):
    """A weekly bar's identity is its week-ending Friday label, which is
    constant Monday through Friday. Deduplicating a weekly signal against the
    *daily* frame's last date re-alerts it every weekday of its entry week."""
    cfg = load_config("config.yaml")
    tuesday = _weekly_entry_bars(pd.Timestamp("2025-06-10"), 1400)
    wednesday = _weekly_entry_bars(pd.Timestamp("2025-06-11"), 1401)
    friday_label = str(pd.Timestamp("2025-06-13").date())

    universe = [Ticker(symbol="WK", sector="Technology", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)
    state_path = tmp_path / "alerts.json"

    run(cfg, _StubClient({"WK": tuesday}, calendar=tuesday.index),
        state_path=state_path)
    assert len(sent) == 1
    assert "weekly" in sent[0]
    saved = json.loads(state_path.read_text())
    assert saved["alerts"]["WK:weekly"]["last_alert_date"] == friday_label

    # Same week, next weekday: the weekly bar is the same bar.
    run(cfg, _StubClient({"WK": wednesday}, calendar=wednesday.index),
        state_path=state_path)
    assert failures == []
    assert len(sent) == 1, "the weekly bar re-alerted on the next weekday"


def test_weekly_signal_carries_its_own_bar_date():
    """The bar date must be the week-ending Friday, not the daily last bar."""
    cfg = load_config("config.yaml")
    bars = _weekly_entry_bars(pd.Timestamp("2025-06-10"), 1400)
    signals = evaluate_symbol("WK", "Technology", 5e9, bars, cfg)
    weekly = next(s for s in signals if s.timeframe == "weekly")
    assert weekly.bar_date == "2025-06-13"
    assert weekly.bar_date != str(bars.index[-1].date())


# --- C2: an empty scan must never look like a quiet market ----------------


def test_empty_universe_raises_and_alerts(monkeypatch, tmp_path):
    """Nasdaq's screener is an unversioned scraped endpoint. A renamed field
    yields zero tickers, and a zero-symbol scan is silent -- indistinguishable
    from a market with no new touches."""
    sent, failures = _wire(monkeypatch, [])
    calendar = pd.bdate_range(end=pd.Timestamp("2025-06-10"), periods=300)
    state_path = tmp_path / "alerts.json"

    with pytest.raises(RuntimeError, match="0 tickers"):
        run(load_config("config.yaml"), _StubClient(calendar=calendar),
            state_path=state_path)

    assert len(failures) == 1
    assert sent == []
    assert not state_path.exists(), "a failed run must not commit state"


def test_no_symbols_in_scanned_sectors_raises(monkeypatch, tmp_path):
    universe = [Ticker(symbol="ODD", sector="Miscellaneous", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)
    calendar = pd.bdate_range(end=pd.Timestamp("2025-06-10"), periods=300)

    with pytest.raises(RuntimeError, match="0 symbols in scanned sectors"):
        run(load_config("config.yaml"), _StubClient(calendar=calendar),
            state_path=tmp_path / "alerts.json")

    assert len(failures) == 1
    assert sent == []


def test_bar_fetch_returning_nothing_raises(monkeypatch, tmp_path):
    """Alpaca answering 200 with no bars for every symbol must not be
    reported as a quiet market."""
    universe = [
        Ticker(symbol=f"S{i}", sector="Technology", market_cap=5e9) for i in range(4)
    ]
    sent, failures = _wire(monkeypatch, universe)
    calendar = pd.bdate_range(end=pd.Timestamp("2025-06-10"), periods=300)
    client = _StubClient(calendar=calendar, missing={f"S{i}" for i in range(4)})

    with pytest.raises(RuntimeError, match="4/4 symbols returned no bars"):
        run(load_config("config.yaml"), client, state_path=tmp_path / "alerts.json")

    assert len(failures) == 1
    assert sent == []


# --- I3: a missing sector ETF must not silently drop its sector -----------


def test_missing_sector_etf_raises(monkeypatch, tmp_path):
    """The eleven sector ETFs are highly liquid; one returning no bars is a
    data-source failure. Dropping it would hide the sector from the
    leaderboard and every stock in it from the scan, indefinitely."""
    cfg = load_config("config.yaml")
    universe = [Ticker(symbol="AAA", sector="Technology", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)
    calendar = pd.bdate_range(end=pd.Timestamp("2025-06-10"), periods=300)
    client = _StubClient(calendar=calendar, missing={cfg.sector_etfs["Utilities"]})

    with pytest.raises(RuntimeError, match="XLU"):
        run(cfg, client, state_path=tmp_path / "alerts.json")

    assert len(failures) == 1
    assert sent == []


# --- I2: the bot token must never reach a public Actions log --------------


def test_telegram_failure_does_not_leak_the_bot_token(monkeypatch, tmp_path):
    """`raise_for_status` builds its message from the full request URL, which
    embeds the bot token. That message reaches the failure alert and is then
    re-raised to stderr in a public repository's Actions log."""
    token = "1234567:AAHsuperSecretBotToken"
    bars = _rally_then_one_day_dip()
    universe = [Ticker(symbol="RALLY", sector="Technology", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    def _explode(text, token_, chat_id):
        raise requests.exceptions.HTTPError(
            "429 Client Error: Too Many Requests for url: "
            f"https://api.telegram.org/bot{token_}/sendMessage"
        )

    monkeypatch.setattr("screener.main.send_telegram", _explode)

    state_path = tmp_path / "alerts.json"
    state_path.write_text(
        json.dumps(
            {"last_run": f"{bars.index[-3].date()}T21:30:00+00:00", "alerts": {}}
        )
    )

    with pytest.raises(requests.exceptions.HTTPError) as caught:
        run(load_config("config.yaml"), _StubClient({"RALLY": bars},
            calendar=bars.index), state_path=state_path)

    assert sent == []
    assert len(failures) == 1
    assert token not in failures[0], "the token leaked into the failure alert"
    assert token not in str(caught.value), "the token leaked into the re-raise"
    assert "***" in str(caught.value)


# --- I4: the skip count must be accurate and always available -------------


def test_insufficient_history_symbols_are_counted_and_netted_out(
    monkeypatch, tmp_path
):
    """Symbols with bars but fewer than `ema_length` of them are skipped
    inside evaluate_symbol. Spec section 11 requires them counted, and the
    scanned figure must not double-count them."""
    cfg = load_config("config.yaml")
    bars = _rally_then_one_day_dip()
    short = bars.iloc[-(cfg.ema_length - 1):]
    universe = [
        Ticker(symbol="RALLY", sector="Technology", market_cap=5e9),
        Ticker(symbol="SHORT", sector="Technology", market_cap=5e9),
    ]
    sent, failures = _wire(monkeypatch, universe)
    client = _StubClient({"RALLY": bars, "SHORT": short}, calendar=bars.index)

    state_path = tmp_path / "alerts.json"
    state_path.write_text(
        json.dumps(
            {"last_run": f"{bars.index[-3].date()}T21:30:00+00:00", "alerts": {}}
        )
    )

    run(cfg, client, state_path=state_path)

    assert failures == []
    assert len(sent) == 1
    assert "Scanned 1 symbols, skipped 1" in sent[0]


def test_skip_count_is_recorded_even_with_no_signals(monkeypatch, tmp_path):
    """A zero-signal run sends nothing, by design. The skip count must still
    land somewhere durable -- the state file is committed every weekday."""
    cfg = load_config("config.yaml")
    calendar = pd.bdate_range(end=pd.Timestamp("2025-06-10"), periods=300)
    flat = _frame(calendar, np.full(len(calendar), 100.0))
    universe = [
        Ticker(symbol="FLAT", sector="Technology", market_cap=5e9),
        Ticker(symbol="TINY", sector="Technology", market_cap=5e9),
    ]
    sent, failures = _wire(monkeypatch, universe)
    client = _StubClient(
        {"FLAT": flat, "TINY": flat.iloc[-10:]}, calendar=calendar
    )

    state_path = tmp_path / "alerts.json"
    run(cfg, client, state_path=state_path)

    assert sent == []
    assert failures == []
    saved = json.loads(state_path.read_text())
    assert saved["last_scan"] == {"scanned": 1, "skipped": 1}


def test_state_without_last_run_does_not_crash(monkeypatch, tmp_path):
    """load_state only guarantees an `alerts` key: a hand-edited state file
    may carry no `last_run` at all."""
    cfg = load_config("config.yaml")
    calendar = pd.bdate_range(end=pd.Timestamp("2025-06-10"), periods=300)
    flat = _frame(calendar, np.full(len(calendar), 100.0))
    universe = [Ticker(symbol="FLAT", sector="Technology", market_cap=5e9)]
    sent, failures = _wire(monkeypatch, universe)

    state_path = tmp_path / "alerts.json"
    state_path.write_text(json.dumps({"alerts": {}}))

    assert run(cfg, _StubClient({"FLAT": flat}, calendar=calendar),
               state_path=state_path) == 0
    assert failures == []
    assert sent == []
