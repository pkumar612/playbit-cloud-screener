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
