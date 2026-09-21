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
    # The date of the bar that fired, on that signal's own timeframe. A weekly
    # bar is labelled by its week-ending Friday, so it is stable Monday to
    # Friday -- which is exactly what deduplication needs.
    bar_date: str


def redact(text: str, *secrets: str) -> str:
    """Strip credentials out of text bound for a log or a re-raise.

    The bot token is embedded in every Telegram URL, so requests' own
    exception messages carry it. This repository is public and its Actions
    logs are world-readable.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def redact_exception(exc: BaseException, *secrets: str) -> None:
    """Redact secrets from an exception's message, in place.

    Every arg is stringified rather than filtered by type. `requests` builds
    connection failures as `ConnectionError(MaxRetryError_object, request=…)`,
    whose only arg is not a string -- yet `str(exc)` still renders that
    object, URL and token included. Skipping non-strings made this a no-op
    for exactly the family that carries the secret.
    """
    exc.args = tuple(redact(str(arg), *secrets) for arg in exc.args)


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
        # A sector holding signals but absent from the ranking would otherwise
        # vanish from the message with no error. Silently losing an alert is
        # the one outcome this tool must never produce, so append the
        # stragglers rather than drop them.
        ordered += sorted(set(by_sector) - set(ordered))
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
        # A single line longer than the limit cannot be accumulated into a
        # valid chunk, so hard-split it. This is reachable through
        # send_failure, whose reason string is arbitrary and need not contain
        # newlines -- and that is the path where a delivery failure costs
        # most, since it would leave the user with silence after a crash.
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
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
        try:
            response = requests.post(
                url, json={"chat_id": chat_id, "text": part}, timeout=30
            )
            response.raise_for_status()
        except Exception as exc:
            # Both HTTPError and the connection errors quote the full URL,
            # token included, in their message.
            redact_exception(exc, token)
            # The wrapped urllib3 error carries the URL too, and Python prints
            # a chained exception regardless of how clean the outer one is.
            # Dropping the chain is the only way to keep it off stderr.
            exc.__cause__ = None
            exc.__suppress_context__ = True
            raise


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
