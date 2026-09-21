from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from screener.config import Config, load_config
from screener.data import AlpacaClient
from screener.indicators import (
    cloud_bounds,
    is_above_cloud,
    mark_cloud_state,
    to_weekly,
)
from screener.notify import (
    Signal,
    format_message,
    redact,
    redact_exception,
    send_failure,
    send_telegram,
    telegram_credentials_from_env,
)
from screener.sectors import rank_sectors, sector_breadth
from screener.state import (
    load_state,
    lookback_bars,
    new_signal_index,
    save_state,
)
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
    lookback: int = 1,
) -> list[Signal]:
    """Return new cloud-entry signals for one symbol across all timeframes.

    `lookback` is how many trailing bars to search for the entry. It is 1 on a
    punctual run; after a missed or failed run it covers the elapsed sessions,
    so the touch is reported late rather than lost.
    """
    frames: dict[str, pd.DataFrame] = {"daily": daily}
    if "weekly" in cfg.timeframes:
        frames["weekly"] = to_weekly(daily)

    signals: list[Signal] = []
    for timeframe in cfg.timeframes:
        bars = frames.get(timeframe)
        if bars is None or len(bars) < cfg.ema_length:
            continue

        marked = _prepare(bars, cfg.ema_length)
        position = new_signal_index(marked, cfg.quiet_bars[timeframe], lookback)
        if position is None:
            continue

        bar = marked.iloc[position]
        signals.append(
            Signal(
                symbol=symbol,
                sector=sector,
                timeframe=timeframe,
                close=float(bar["close"]),
                market_cap=market_cap,
                ema_top=float(bar["ema_top"]),
                ema_bot=float(bar["ema_bot"]),
                low_confidence=len(bars) < cfg.ema_length * MIN_CONVERGENCE_MULTIPLE,
                bar_date=str(marked.index[position].date()),
            )
        )
    return signals


def run(cfg: Config, client: AlpacaClient, state_path: Path = STATE_PATH) -> int:
    token, chat_id = telegram_credentials_from_env()

    try:
        universe = fetch_universe(cfg.min_market_cap)
        # An empty universe is silence indistinguishable from a quiet market,
        # so it must be an error. Nasdaq's screener is unversioned and scraped.
        if not universe:
            raise RuntimeError(
                "universe fetch returned 0 tickers -- Nasdaq schema likely changed"
            )

        etf_frames = client.daily_bars(
            list(cfg.sector_etfs.values()) + [cfg.benchmark], start=HISTORY_START
        )
        # The eleven sector ETFs and the benchmark are among the most liquid
        # symbols listed. One missing is a data-source failure, not a partial
        # symbol failure -- and dropping it would hide a whole sector.
        missing = [
            symbol
            for symbol in list(cfg.sector_etfs.values()) + [cfg.benchmark]
            if symbol not in etf_frames or etf_frames[symbol].empty
        ]
        if missing:
            raise RuntimeError(f"no bars for index symbols: {', '.join(missing)}")

        benchmark = etf_frames[cfg.benchmark]["close"]
        etf_closes = {
            sector: etf_frames[etf]["close"]
            for sector, etf in cfg.sector_etfs.items()
        }
        ranking = rank_sectors(etf_closes, benchmark, cfg.rs_weights)
        scanned_sectors = [s for s, _ in ranking[: cfg.top_n_sectors]]

        selected = [t for t in universe if t.sector in scanned_sectors]
        if not selected:
            raise RuntimeError(f"0 symbols in scanned sectors {scanned_sectors}")

        frames = client.daily_bars([t.symbol for t in selected], start=HISTORY_START)

        state = load_state(state_path)
        # Cover every session since the last successful run, so a dropped cron
        # or a failed run reports its touches late instead of losing them.
        lookback = lookback_bars(etf_frames[cfg.benchmark].index, state.get("last_run"))

        signals: list[Signal] = []
        above: dict[str, bool] = {}
        skipped = 0

        for ticker in selected:
            daily = frames.get(ticker.symbol)
            # Too little history to compute the EMA at all is a skip, and
            # spec section 11 requires skips to be counted.
            if daily is None or daily.empty or len(daily) < cfg.ema_length:
                skipped += 1
                continue

            reading = is_above_cloud(daily, cfg.ema_length)
            if reading is not None:
                above[ticker.symbol] = reading

            for signal in evaluate_symbol(
                ticker.symbol, ticker.sector, ticker.market_cap, daily, cfg,
                lookback=lookback,
            ):
                key = f"{signal.symbol}:{signal.timeframe}"
                # Dedup on the firing bar's own date, on its own timeframe. A
                # weekly bar keeps its week-ending Friday label all week, so
                # it alerts once rather than every weekday.
                prior = state["alerts"].get(key, {}).get("last_alert_date")
                if prior is not None and signal.bar_date <= prior:
                    continue
                state["alerts"][key] = {"last_alert_date": signal.bar_date}
                signals.append(signal)

        if skipped > len(selected) // 2:
            raise RuntimeError(f"{skipped}/{len(selected)} symbols returned no bars")

        breadth = sector_breadth(
            {t.symbol: t.sector for t in selected}, above
        )

        if signals:
            send_telegram(
                format_message(
                    signals, ranking, breadth, scanned_sectors,
                    len(selected) - skipped, skipped,
                ),
                token,
                chat_id,
            )

        # Always stamp last_run so the state file changes every weekday and
        # the commit keeps the public repo's cron schedule alive. The scan
        # counts ride along, so a zero-signal run still records its skips
        # where they can be read without breaking the "no message means no
        # new touches" contract.
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        state["last_scan"] = {"scanned": len(selected) - skipped, "skipped": skipped}
        save_state(state_path, state)
        return 0

    except Exception as exc:
        # The bot token is embedded in every Telegram URL and therefore in
        # requests' exception messages. This repository is public: neither the
        # failure alert nor the re-raise may carry it.
        redact_exception(exc, token)
        send_failure(redact(traceback.format_exc(limit=3), token), token, chat_id)
        raise


def main() -> int:
    cfg = load_config("config.yaml")
    return run(cfg, AlpacaClient.from_env())


if __name__ == "__main__":
    sys.exit(main())
