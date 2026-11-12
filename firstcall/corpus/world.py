"""The demo company as the generator and the seed command read it from config/demo.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEMO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "demo.yaml"


@dataclass(frozen=True)
class AgentInfo:
    name: str
    email: str | None
    phone: str | None


@dataclass(frozen=True)
class World:
    raw: dict[str, Any]

    @property
    def teams(self) -> list[dict[str, Any]]:
        return list(self.raw["teams"])

    @property
    def item_team(self) -> dict[str, str]:
        return {item: t["slug"] for t in self.teams for item in t["items"]}

    @property
    def agents(self) -> list[AgentInfo]:
        return [AgentInfo(a["name"], a.get("email"), a.get("phone")) for a in self.raw["agents"]]

    def source(self, slug: str) -> dict[str, Any]:
        return next(s for s in self.raw["sources"] if s["slug"] == slug)


def load_world(path: Path = DEMO_CONFIG) -> World:
    return World(yaml.safe_load(path.read_text()))
