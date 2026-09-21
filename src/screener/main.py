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
    send_failure,
    send_telegram,
    telegram_credentials_from_env,
)
from screener.sectors import rank_sectors, sector_breadth
from screener.state import is_new_signal, load_state, save_state
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
) -> list[Signal]:
    """Return new cloud-entry signals for one symbol across all timeframes."""
    frames: dict[str, pd.DataFrame] = {"daily": daily}
    if "weekly" in cfg.timeframes:
        frames["weekly"] = to_weekly(daily)

    signals: list[Signal] = []
    for timeframe in cfg.timeframes:
        bars = frames.get(timeframe)
        if bars is None or len(bars) < cfg.ema_length:
            continue

        marked = _prepare(bars, cfg.ema_length)
        if not is_new_signal(marked, cfg.quiet_bars[timeframe]):
            continue

        last = marked.iloc[-1]
        signals.append(
            Signal(
                symbol=symbol,
                sector=sector,
                timeframe=timeframe,
                close=float(last["close"]),
                market_cap=market_cap,
                ema_top=float(last["ema_top"]),
                ema_bot=float(last["ema_bot"]),
                low_confidence=len(bars) < cfg.ema_length * MIN_CONVERGENCE_MULTIPLE,
            )
        )
    return signals


def run(cfg: Config, client: AlpacaClient, state_path: Path = STATE_PATH) -> int:
    token, chat_id = telegram_credentials_from_env()

    try:
        universe = fetch_universe(cfg.min_market_cap)

        etf_frames = client.daily_bars(
            list(cfg.sector_etfs.values()) + [cfg.benchmark], start=HISTORY_START
        )
        benchmark = etf_frames[cfg.benchmark]["close"]
        etf_closes = {
            sector: etf_frames[etf]["close"]
            for sector, etf in cfg.sector_etfs.items()
            if etf in etf_frames
        }
        ranking = rank_sectors(etf_closes, benchmark, cfg.rs_weights)
        scanned_sectors = [s for s, _ in ranking[: cfg.top_n_sectors]]

        selected = [t for t in universe if t.sector in scanned_sectors]
        frames = client.daily_bars([t.symbol for t in selected], start=HISTORY_START)

        state = load_state(state_path)
        signals: list[Signal] = []
        above: dict[str, bool] = {}
        skipped = 0

        for ticker in selected:
            daily = frames.get(ticker.symbol)
            if daily is None or daily.empty:
                skipped += 1
                continue

            reading = is_above_cloud(daily, cfg.ema_length)
            if reading is not None:
                above[ticker.symbol] = reading

            for signal in evaluate_symbol(
                ticker.symbol, ticker.sector, ticker.market_cap, daily, cfg
            ):
                key = f"{signal.symbol}:{signal.timeframe}"
                today = str(daily.index[-1].date())
                if state["alerts"].get(key, {}).get("last_alert_date") == today:
                    continue
                state["alerts"][key] = {
                    "last_alert_date": today,
                    "last_touch_date": today,
                }
                signals.append(signal)

        breadth = sector_breadth(
            {t.symbol: t.sector for t in selected}, above
        )

        if signals:
            send_telegram(
                format_message(
                    signals, ranking, breadth, scanned_sectors,
                    len(selected), skipped,
                ),
                token,
                chat_id,
            )

        # Always stamp last_run so the state file changes every weekday and
        # the commit keeps the public repo's cron schedule alive.
        state["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(state_path, state)
        return 0

    except Exception:
        send_failure(traceback.format_exc(limit=3), token, chat_id)
        raise


def main() -> int:
    cfg = load_config("config.yaml")
    return run(cfg, AlpacaClient.from_env())


if __name__ == "__main__":
    sys.exit(main())
