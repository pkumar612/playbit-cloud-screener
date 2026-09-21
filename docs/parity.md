# TradingView Parity

Run `python scripts/parity_check.py AAPL MSFT NVDA JPM XOM` and record the
results against the values read off the TradingView chart.

| Symbol | TF | TV top | Computed top | TV bot | Computed bot | Δ |
|---|---|---|---|---|---|---|
| | | | | | | |

**Expectation.** Daily should match to within a few cents: ~2,500 bars against
a 200-period EMA is 12x the period, so the seed value has fully decayed.

**Weekly is expected to differ** and the difference should be recorded here.
Alpaca's free tier starts in 2016, giving ~520 weekly bars — 2.6x the period,
where the seed still contributes. TradingView seeds from deeper history. If the
recorded difference is material, implement the yfinance deep-history backfill
described in section 13 of the design spec.
