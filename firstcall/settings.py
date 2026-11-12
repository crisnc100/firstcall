"""Process settings, read from FIRSTCALL_* environment variables."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOCAL_DATABASE_URL = (
    "postgresql+psycopg://firstcall:firstcall@127.0.0.1:57432/firstcall"  # pragma: allowlist secret
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FIRSTCALL_", extra="ignore")

    env: Literal["local", "test", "beta"] = "local"
    database_url: str = LOCAL_DATABASE_URL
    failpoint: str = ""
    # X-Forwarded-For is attacker-controlled unless a trusted proxy sets it.
    trust_proxy: bool = False
    # How many proxies in front of the api append to X-Forwarded-For. The client address is
    # that many entries from the right; entries further left are whatever the client sent.
    trusted_hops: int = 1
    job_lease_seconds: int = 60
    max_attempts: int = 5
    source_rate_per_minute: int = 60
    ip_rate_per_minute: int = 30
    form_max_bytes: int = 1_000_000
    email_max_bytes: int = 10_000_000

    @model_validator(mode="after")
    def _no_failpoints_in_beta(self) -> Settings:
        if self.env == "beta" and self.failpoint:
            raise ValueError("FIRSTCALL_FAILPOINT is refused when FIRSTCALL_ENV=beta")
        return self
