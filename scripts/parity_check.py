"""Print the computed cloud bounds so they can be compared against TradingView.

Usage:
    python scripts/parity_check.py AAPL MSFT NVDA

On the TradingView chart, add the PB-EMA indicator, hover the most recent
candle, and read "PB EMA Top" and "PB EMA Bot" from the status line. They
should match the daily values printed here to within a few cents.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path so screener module can be imported
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from screener.config import load_config
from screener.data import AlpacaClient
from screener.indicators import cloud_bounds, to_weekly
from screener.main import HISTORY_START, MIN_CONVERGENCE_MULTIPLE


def main(symbols: list[str]) -> int:
    cfg = load_config("config.yaml")
    frames = AlpacaClient.from_env().daily_bars(symbols, start=HISTORY_START)

    for symbol in symbols:
        daily = frames.get(symbol)
        if daily is None or len(daily) < cfg.ema_length:
            print(f"{symbol}: insufficient history")
            continue

        print(f"\n{symbol}")
        for label, bars in (("daily", daily), ("weekly", to_weekly(daily))):
            if len(bars) < cfg.ema_length:
                print(f"  {label:6}: insufficient history ({len(bars)} bars)")
                continue
            last = cloud_bounds(bars, cfg.ema_length).iloc[-1]
            warn = (
                "  ⚠️ not converged"
                if len(bars) < cfg.ema_length * MIN_CONVERGENCE_MULTIPLE
                else ""
            )
            print(
                f"  {label:6}: date={bars.index[-1].date()} "
                f"close={last['close']:.2f} "
                f"top={last['ema_top']:.4f} bot={last['ema_bot']:.4f} "
                f"bars={len(bars)}{warn}"
            )
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
