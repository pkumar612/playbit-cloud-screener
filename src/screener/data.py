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
