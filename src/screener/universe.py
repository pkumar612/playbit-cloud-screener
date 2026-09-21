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
