"""Model prices from the committed snapshot in config/model_prices.yaml.

The budget pre-check prices a call before it is made, so a model with no listed price is
never called in live or record mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

PRICES = Path(__file__).resolve().parents[2] / "config" / "model_prices.yaml"
MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    prompt_per_million: Decimal
    completion_per_million: Decimal

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return (
            self.prompt_per_million * prompt_tokens
            + self.completion_per_million * completion_tokens
        ) / MILLION


def load_prices(path: Path = PRICES) -> dict[str, Price]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return {
        str(model): Price(Decimal(str(row["prompt"])), Decimal(str(row["completion"])))
        for model, row in data["models"].items()
    }
