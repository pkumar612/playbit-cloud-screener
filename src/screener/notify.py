from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

TELEGRAM_MAX_CHARS = 4096


@dataclass(frozen=True)
class Signal:
    symbol: str
    sector: str
    timeframe: str
    close: float
    market_cap: float
    ema_top: float
    ema_bot: float
    low_confidence: bool


def _human_cap(market_cap: float) -> str:
    if market_cap >= 1e12:
        return f"${market_cap / 1e12:.1f}T"
    if market_cap >= 1e9:
        return f"${market_cap / 1e9:.1f}B"
    return f"${market_cap / 1e6:.0f}M"


def format_message(
    signals: list[Signal],
    ranking: list[tuple[str, float]],
    breadth: dict[str, float],
    scanned_sectors: list[str],
    scanned_count: int,
    skipped_count: int,
) -> str:
    lines = ["☁️ PlayBit Cloud Touches", "", "Sector strength (✅ = scanned):"]

    for sector, score in ranking:
        mark = "✅" if sector in scanned_sectors else "  "
        pct = breadth.get(sector)
        breadth_text = f"breadth {pct:.1f}%" if pct is not None else "breadth n/a"
        lines.append(f"{mark} {sector}: RS {score:+.2f}, {breadth_text}")

    lines.append("")

    if not signals:
        lines.append("No new touches.")
    else:
        by_sector: dict[str, list[Signal]] = defaultdict(list)
        for signal in signals:
            by_sector[signal.sector].append(signal)

        # Preserve ranking order so the strongest sector appears first.
        ordered = [s for s, _ in ranking if s in by_sector]
        for sector in ordered:
            lines.append(f"— {sector} —")
            for signal in sorted(by_sector[sector], key=lambda s: s.symbol):
                flag = " ⚠️" if signal.low_confidence else ""
                lines.append(
                    f"  {signal.symbol} ({signal.timeframe}){flag} "
                    f"@ {signal.close:.2f} | cap {_human_cap(signal.market_cap)} | "
                    f"cloud {signal.ema_bot:.2f}–{signal.ema_top:.2f}"
                )
            lines.append("")

    lines.append(
        f"Scanned {scanned_count} symbols, skipped {skipped_count} "
        f"· {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    )
    if any(s.low_confidence for s in signals):
        lines.append("⚠️ = EMA not fully converged (limited history)")
    return "\n".join(lines)


def _chunk(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text: str, token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for part in _chunk(text):
        response = requests.post(
            url, json={"chat_id": chat_id, "text": part}, timeout=30
        )
        response.raise_for_status()


def send_failure(reason: str, token: str, chat_id: str) -> None:
    """A failed run must never be mistaken for a quiet market."""
    send_telegram(f"🚨 Screener run FAILED\n\n{reason}", token, chat_id)


def telegram_credentials_from_env() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set"
        )
    return token, chat_id
