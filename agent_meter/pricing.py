"""Prices. Nothing is guessed: what is not declared is not priced."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

from .usage import Usage


class Prices:
    """Dollars per million tokens, with an optional peak window."""

    def __init__(self, config: dict):
        self.entries = config.get("price", [])
        self.peak = config.get("peak") or {}

    @classmethod
    def load(cls, path: str | Path) -> "Prices":
        with open(path, "rb") as f:
            return cls(tomllib.load(f))

    def _entry(self, model: str | None) -> dict | None:
        for e in self.entries:
            pattern = e.get("match", "")
            if pattern and pattern in (model or ""):
                return e
        for e in self.entries:
            if not e.get("match"):
                return e
        return None

    def _in_peak(self, ts: float) -> bool:
        if not self.peak:
            return False
        t = datetime.fromtimestamp(ts, UTC)
        if self.peak.get("weekdays_only") and t.weekday() >= 5:
            return False
        return any(start <= t.hour < end for start, end in self.peak.get("hours_utc", []))

    def cost(self, usage: Usage, ts: float, model: str | None) -> float | None:
        """Cost in dollars, or None when no price matches the model.

        None is not zero. It means "I do not know", and the report says so
        rather than adding a misleading zero to the total.
        """
        e = self._entry(model)
        if not e:
            return None
        factor = self.peak.get("multiplier", 1.0) if self._in_peak(ts) else 1.0
        write_rate = e.get("cache_write", e.get("cache_miss", 0.0))
        total = (
            usage.cache_read * e.get("cache_hit", 0.0)
            + usage.input_fresh * e.get("cache_miss", 0.0)
            + usage.cache_write * write_rate
            + usage.output * e.get("output", 0.0)
        )
        return total * factor / 1_000_000
