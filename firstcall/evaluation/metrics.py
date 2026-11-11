"""Rates with Wilson 95% intervals, the uncertainty range used in every report."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

Z95 = 1.959963984540054


def wilson(successes: int, total: int) -> tuple[float, float] | None:
    """The Wilson score interval for successes/total at 95%, or None when total is 0."""
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + Z95**2 / total
    centre = (p + Z95**2 / (2 * total)) / denominator
    half = Z95 * math.sqrt(p * (1 - p) / total + Z95**2 / (4 * total**2)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class Rate:
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return self.hits / self.total if self.total else None

    def as_json(self) -> dict[str, Any]:
        interval = wilson(self.hits, self.total)
        return {
            "hits": self.hits,
            "total": self.total,
            "rate": None if self.value is None else round(self.value, 4),
            "interval": None
            if interval is None
            else [round(interval[0], 4), round(interval[1], 4)],
        }

    def text(self) -> str:
        if not self.total:
            return "n/a (0 cases)"
        low, high = wilson(self.hits, self.total) or (0.0, 0.0)
        return f"{self.hits}/{self.total} = {self.value:.3f} [{low:.3f}, {high:.3f}]"
