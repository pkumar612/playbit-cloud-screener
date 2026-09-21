from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

SUPPORTED_TIMEFRAMES = ("daily", "weekly")


@dataclass(frozen=True)
class Config:
    min_market_cap: int
    top_n_sectors: int
    ema_length: int
    quiet_bars: dict[str, int]
    rs_weights: dict[str, float]
    timeframes: list[str]
    benchmark: str
    sector_etfs: dict[str, str]


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())

    weight_total = sum(raw["rs_weights"].values())
    if abs(weight_total - 1.0) > 1e-9:
        raise ValueError(f"rs_weights must sum to 1.0, got {weight_total}")

    for timeframe in raw["timeframes"]:
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; "
                f"supported: {', '.join(SUPPORTED_TIMEFRAMES)}"
            )

    return Config(**raw)
