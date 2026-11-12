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
    # A tiered price: a call whose prompt is above `long_above` tokens is charged the long
    # rates for all of its tokens, prompt and completion alike.
    long_above: int | None = None
    long: Price | None = None

    def cost(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        if (
            self.long is not None
            and self.long_above is not None
            and prompt_tokens > self.long_above
        ):
            return self.long.cost(prompt_tokens, completion_tokens)
        return (
            self.prompt_per_million * prompt_tokens
            + self.completion_per_million * completion_tokens
        ) / MILLION


def _price(row: dict[str, Any]) -> Price:
    tier = row.get("long")
    return Price(
        Decimal(str(row["prompt"])),
        Decimal(str(row["completion"])),
        long_above=int(tier["above"]) if tier else None,
        long=_price(tier) if tier else None,
    )


def load_prices(path: Path = PRICES) -> dict[str, Price]:
    data: dict[str, Any] = yaml.safe_load(path.read_text())
    return {str(model): _price(row) for model, row in data["models"].items()}
