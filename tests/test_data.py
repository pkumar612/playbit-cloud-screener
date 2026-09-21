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
