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
