# PlayBit EMA Cloud Sector Screener — Design

**Date:** 2026-09-21
**Status:** Approved, ready for implementation planning

## 1. Purpose

Alert the user when mid- and large-cap US stocks in currently-trending sectors
make contact with the "PlayBit EMA cloud" — the band between `EMA(high, 200)`
and `EMA(close, 200)`.

The user runs this indicator on TradingView but is on the free plan, which
cannot deliver the alerts they want. This system reproduces the indicator's
math outside TradingView and pushes alerts to Telegram on a schedule, at zero
cost.

## 2. Background: why not TradingView

TradingView cannot do this on any plan, not just the free one:

- The free/Basic plan allows roughly 3 price alerts and **zero** technical
  (indicator-based) alerts. The cloud-touch condition is a technical condition.
- Even the Premium plan caps technical alerts at 400. Alerts are one-per-symbol,
  so a universe of thousands of tickers exceeds every tier.
- Pine Screener, which would scan a custom indicator across a universe, requires
  a paid plan, caps at 4,000 symbols, scans one watchlist at a time, and is a
  manual screener rather than an alert engine — it does not push notifications.

The indicator is pure EMA arithmetic with no repainting and no proprietary data,
so it reproduces exactly outside TradingView. Reimplementation is the correct
approach, not a workaround.

## 3. Scope

### In scope

- Daily and weekly timeframes.
- Mid- and large-cap US common stocks (market cap >= $2B).
- Restriction to the top 4 sectors by relative strength.
- Telegram delivery.
- Scheduled execution on GitHub Actions, post-close on weekdays.

### Out of scope

- **Monthly timeframe.** `EMA(200)` on monthly bars requires 200 monthly bars —
  16.7 years — merely to exist, and roughly three times that before the EMA
  stops depending on its seed value. Alpaca's free tier only reaches back to
  2016 (~120 monthly bars), so monthly is not computable there at all. It is
  deliberately excluded rather than shipped as an unreliable number.
- Intraday timeframes. GitHub Actions cron is best-effort and routinely runs
  5–30 minutes late at peak times, which makes intraday candle-close alerts
  unreliable.
- Order placement. This system observes and notifies; it does not trade.
- Backtesting and performance measurement.

## 4. Architecture

A single scheduled batch job, decomposed into modules that each own one concern
and can be tested without network access.

```
.github/workflows/scan.yml     Schedule, secrets, state commit
src/screener/
  universe.py                  Nasdaq screener -> cap + sector filtered tickers
  data.py                      Alpaca bar fetching, chunking, pagination
  indicators.py                EMA, cloud bounds, touch detection
  sectors.py                   Relative-strength ranking and breadth
  state.py                     Cross-run alert deduplication
  notify.py                    Telegram message formatting and delivery
  main.py                      Orchestration
tests/                         Fixture-driven, no network
state/alerts.json              Committed back to the repo each run
```

### Component contracts

**`universe.py`** — Fetches the Nasdaq screener endpoint
(`api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10&download=true`;
`download=true` causes the endpoint to ignore `limit` and return the full table),
which returns every US-listed stock with symbol, market cap, sector and industry
in a single ~2.2 MB response. Filters to `marketCap >= 2e9`, excludes ETFs and
test issues, and normalises Nasdaq's sector names to the eleven-sector taxonomy
the user is familiar with. Returns a list of `(symbol, sector, market_cap)`.
Depends on: HTTP only.

**`data.py`** — Given a list of symbols, returns a daily OHLCV frame per symbol
from Alpaca's multi-symbol bars endpoint. Owns symbol chunking, `next_page_token`
pagination, retry with backoff, and rate limiting to stay under the Basic plan's
200 requests/minute. Depends on: Alpaca credentials.

**`indicators.py`** — Pure functions over a single symbol's OHLC frame. No I/O,
no network, no configuration. Computes cloud bounds, touch, clear, and the
weekly resample. This is where correctness matters most, so it is the most
heavily tested module.

**`sectors.py`** — Given sector ETF bars and SPY bars, returns sectors ranked by
relative strength. Given the scanned universe's cloud states, returns per-sector
breadth. Pure functions over frames.

**`state.py`** — Loads and saves `state/alerts.json`, mapping
`(symbol, timeframe)` to the date of the last alert and the last observed cloud
state. Decides whether a touch is new. Pure logic plus one file read/write.

**`notify.py`** — Formats the alert into a Telegram message and sends it.
Depends on: Telegram credentials.

## 5. Data flow

1. Fetch universe (1 HTTP call) -> ~2,197 symbols with sector and market cap.
2. Fetch daily bars for the 11 sector ETFs (XLK, XLF, XLI, XLC, XLV, XLY, XLE,
   XLP, XLB, XLRE, XLU) plus SPY.
3. Rank sectors by relative strength; select the top 4.
4. Narrow the universe to symbols in those sectors -> ~900–1,200 symbols.
5. Fetch daily bars for those symbols from 2016 to today.
6. Compute the cloud and touch state on daily bars; resample to weekly and
   repeat.
7. Compute per-sector breadth from the results of step 6.
8. Load prior state; keep only touches that are newly triggered.
9. Send one grouped Telegram message.
10. Write and commit updated state.

## 6. Signal definition

Transcribed directly from the user's Pine Script with no reinterpretation:

```python
emaTop = high.ewm(span=200, adjust=False).mean()
emaBot = close.ewm(span=200, adjust=False).mean()
touch  = (low <= emaTop) & (high >= emaBot)
```

### EMA seeding

Pine's `ta.ema` seeds from the first source value
(`alpha * src + (1 - alpha) * nz(ema[1])`, `alpha = 2 / (length + 1)`), which is
exactly what pandas `.ewm(span=N, adjust=False).mean()` does. The widely
repeated advice to seed an EMA with an SMA of the first `N` values would **not**
match TradingView and must not be used here.

### Touch definition

The user chose the broadest definition: any overlap between the bar's range and
the cloud band, regardless of where the bar closed. A wick into the cloud counts
equally with a close inside it.

### Deduplication

The broad definition means a stock oscillating around its 200 EMA would alert
every bar for weeks. To prevent this without narrowing the definition:

- A bar is **clear** when it lies entirely outside the band:
  `high < emaBot or low > emaTop`.
- An alert fires on the first touching bar preceded by at least `N` consecutive
  clear bars: `N = 5` daily, `N = 3` weekly.
- After firing, that symbol and timeframe stay silent until the stock goes clear
  again and subsequently returns.

## 7. Sector ranking

```
rs_score = 0.3 * (ret_1m  - spy_ret_1m)
         + 0.4 * (ret_3m  - spy_ret_3m)
         + 0.3 * (ret_6m  - spy_ret_6m)
```

Sectors are ranked by `rs_score` descending; the top 4 are scanned.

Breadth — the percentage of a sector's mid/large caps currently above their own
cloud — is computed from bars already fetched and reported alongside the ranking.
It distinguishes a broadly advancing sector from a cap-weighted ETF being carried
by two megacaps. Breadth is **reported, not used as a filter**, keeping the
selection rule to a single dimension.

## 8. Notification

Telegram, chosen over email: no SMTP configuration, no app passwords, no spam
filtering, and instant delivery. Requires `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` in GitHub Secrets.

Message structure:

- The 11-sector leaderboard with RS scores and breadth, top 4 marked as scanned.
- New touches grouped by sector, each line giving symbol, timeframe, last price,
  market cap, and the cloud band.
- A footer with the run timestamp and the count of symbols scanned.

A run producing no new touches sends nothing, so the absence of a message is
itself information.

## 9. Infrastructure

**Schedule:** `30 21 * * 1-5` (UTC). This is 5:30pm EDT in summer and 4:30pm EST
in winter — after the close year-round from a single cron expression, with no
daylight-saving bug. `workflow_dispatch` is also enabled for manual runs.

**Cost:** roughly 5 minutes per run, about 22 runs per month, ~110 minutes total
against a free-tier allowance of 2,000 minutes/month on private repositories and
unlimited on public ones.

**State persistence:** `state/alerts.json` is committed back to the repository by
the workflow, which requires `permissions: contents: write`. The GitHub Actions
cache was rejected because it evicts after 7 days of inactivity, which would
silently cause stale setups to re-alert.

**Secrets:** `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`.

## 10. Configuration

A single `config.yaml` holds the dials the user is most likely to turn, so that
tuning never requires editing code:

| Setting | Default |
|---|---|
| `min_market_cap` | `2_000_000_000` |
| `top_n_sectors` | `4` |
| `quiet_bars.daily` | `5` |
| `quiet_bars.weekly` | `3` |
| `ema_length` | `200` |
| `rs_weights` | `{1m: 0.3, 3m: 0.4, 6m: 0.3}` |
| `timeframes` | `[daily, weekly]` |

## 11. Error handling

- **Data source failure.** If the Nasdaq screener or Alpaca is unreachable after
  retries, the run aborts without writing state and sends a short failure
  message to Telegram. A failed run must never be mistaken for a quiet market.
- **Partial symbol failure.** Individual symbols that return no or insufficient
  bars are skipped and counted; the run continues. The count appears in the
  message footer.
- **Insufficient history.** A symbol with fewer than `ema_length` bars on a given
  timeframe is skipped for that timeframe, since its EMA cannot be computed at
  all. A symbol with at least `ema_length` but fewer than `3 * ema_length` bars
  is still scanned, but marked low-confidence in the alert, because its EMA has
  not fully converged. Note that the weekly timeframe sits in this band by
  design: Alpaca's 2016 start yields ~520 weekly bars against a 600-bar
  convergence threshold, so weekly signals are expected to carry the
  low-confidence marker until the deep-history backfill described in section 13
  is built. Skipping at 3x would discard every weekly signal.
- **State corruption.** An unparseable `alerts.json` is treated as empty, and the
  run proceeds. The cost is one round of duplicate alerts, which is preferable to
  a crashed run.

## 12. Testing strategy

All tests run from fixtures with no network access, so the suite is
deterministic and runs in CI.

- **`indicators.py`** — EMA output against hand-computed values; touch detection
  against constructed bars covering every case (fully above, fully below, wick
  in from above, wick in from below, engulfing the band, exactly touching a
  boundary); weekly resampling against a known calendar including holiday-short
  weeks.
- **`state.py`** — the quiet-period state machine: first touch fires, repeat
  touches stay silent, re-entry after going clear fires again, boundary at
  exactly `N` clear bars.
- **`sectors.py`** — RS ranking with known returns; breadth arithmetic.
- **`universe.py`** — filtering and sector normalisation against a saved slice of
  a real screener response.
- **`data.py`** — chunking and pagination against a stubbed HTTP layer.

### TradingView parity check

A dedicated test takes 3–5 tickers whose `emaTop` and `emaBot` values the user
reads directly off their TradingView chart, and asserts the computed values are
within tolerance. This is the test that proves the alerts correspond to what the
user actually sees.

## 13. Known limitations

- **Weekly EMA convergence.** Alpaca's free tier reaches back to 2016, giving
  ~520 weekly bars against a 200-period EMA — only 2.6x the period. Daily, at
  ~2,500 bars, is 12x and fully converged. Weekly values may therefore differ
  slightly from TradingView, which seeds from deeper history. The parity check
  will quantify this. If the divergence proves material, the remedy is a
  one-time yfinance deep-history backfill used solely to seed the weekly EMA.
  This is deliberately deferred until measured rather than built speculatively.
- **Sector taxonomy.** Nasdaq's sector labels differ from Yahoo Finance's. They
  map one-to-one for most names, but some megacaps land in different buckets —
  Nasdaq files GOOGL and META under Technology where Yahoo uses Communication
  Services. Sector membership in alerts will therefore not always match what the
  user sees on Yahoo.
- **Free-tier data quality.** Alpaca's Basic plan excludes the most recent 15
  minutes of data, which is irrelevant to a post-close run, and its real-time
  feed is IEX-only. Historical daily bars are not affected.
- **Cron punctuality.** GitHub Actions scheduled runs are best-effort and can be
  delayed. Acceptable for an end-of-day scan; it is the reason intraday is out
  of scope.

## 14. Future work

Not built now, recorded so the decision is not relitigated:

- Monthly timeframe via a yfinance deep-history source.
- Intraday scanning on a small hand-picked watchlist, where the cron delay and
  data volume are both tolerable.
- Using breadth as a filter rather than only as reported context.
- Alpaca paper-trading integration to act on alerts automatically.
