"""Process settings, read from FIRSTCALL_* environment variables."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import SecretStr, model_validator
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
    # The model read. Offline makes no model calls; every message takes the rules path.
    model_mode: Literal["replay", "record", "live", "offline"] = "offline"
    model_name: str = ""
    model_key: SecretStr = SecretStr("")
    # The kill switch, FIRSTCALL_MODEL_OFF=1: no model call is made while it is set.
    model_off: bool = False
    # Spend per org and UTC day, kept in the budget ledger so a restart does not reset it.
    model_daily_cap_usd: Decimal = Decimal("1.00")
    model_timeout_seconds: float = 8.0
    model_max_output_tokens: int = 400
    model_recordings: str = "evals/recordings"
    # Sign-in.
    session_days: int = 7
    signin_rate_per_minute: int = 10
    # Alerts. The inbox backend keeps email in the app; smtp sends to a mail server, which is
    # Mailpit locally.
    email_backend: Literal["smtp", "inbox"] = "inbox"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    email_from: str = "Firstcall <alerts@firstcall.localhost>"
    digest_seconds: int = 60
    # Links in alerts point here.
    base_url: str = "http://localhost:8020"
    # Web push. With no private key, push is off and email still goes out.
    vapid_private_key: SecretStr = SecretStr("")
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:alerts@firstcall.localhost"
    push_retries: int = 3
    # Plain-http push endpoints, for the fake push service in tests and compose only.
    push_allow_http: bool = False

    @model_validator(mode="after")
    def _no_failpoints_in_beta(self) -> Settings:
        if self.env == "beta" and self.failpoint:
            raise ValueError("FIRSTCALL_FAILPOINT is refused when FIRSTCALL_ENV=beta")
        if self.env == "beta" and self.push_allow_http:
            raise ValueError("FIRSTCALL_PUSH_ALLOW_HTTP is refused when FIRSTCALL_ENV=beta")
        return self
