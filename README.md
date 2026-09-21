# PlayBit EMA Cloud Sector Screener

Alerts on Telegram when mid/large-cap US stocks in the strongest sectors touch
the PlayBit EMA cloud — the band between `EMA(high, 200)` and `EMA(close, 200)`.

Runs free on GitHub Actions each weekday after the US close.

## Why not TradingView alerts

TradingView's free plan allows no technical alerts at all, and even Premium caps
them at 400 — one per symbol, against a universe of thousands. Pine Screener
needs a paid plan and is a manual screener, not an alert engine. The indicator
is pure EMA arithmetic, so it reproduces exactly outside TradingView.

## Setup

### 1. Fork or clone, then set four secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Where to get it |
|---|---|
| `ALPACA_API_KEY` | alpaca.markets → Paper account → API keys |
| `ALPACA_SECRET_KEY` | shown once, at the same time |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` |
| `TELEGRAM_CHAT_ID` | see below |

**Never commit these.** This repository is public.

### 2. Get your Telegram chat ID

Message your new bot once, then:

```bash
curl "https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates"
```

Read `result[0].message.chat.id` from the response.

### 3. Trigger a manual run

Actions → scan → Run workflow. You should get a Telegram message within a few
minutes.

## Tuning

Everything adjustable lives in `config.yaml`:

| Setting | Default | Effect |
|---|---|---|
| `min_market_cap` | 2e9 | Lower to include small caps |
| `top_n_sectors` | 4 | How many sectors get scanned |
| `quiet_bars.daily` | 5 | Bars a stock must be clear of the cloud before it can alert again |
| `quiet_bars.weekly` | 3 | Same, weekly |
| `rs_weights` | 30/40/30 | Weighting across 1/3/6-month relative strength |

## Verifying against your chart

```bash
PYTHONPATH=src python scripts/parity_check.py AAPL MSFT NVDA
```

Compare against the PB-EMA values in TradingView's status line. Daily should
match to within a few cents. See `docs/parity.md` for why weekly may not.

## Known limitations

- **No monthly timeframe.** Alpaca's free tier starts in 2016, giving ~120
  monthly bars against the 200 an EMA(200) requires.
- **Weekly EMA is not fully converged** (~520 bars vs 200-period). Weekly
  signals carry a ⚠️ marker.
- **Sector labels come from Nasdaq**, which files GOOGL and META under
  Technology where Yahoo Finance uses Communication Services.
- **Cron is best-effort.** GitHub may run the job late. Fine for end-of-day.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -v
```

Tests never hit the network.
