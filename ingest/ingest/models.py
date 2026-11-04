from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_dt(v: Any) -> Any:
    """Accept ISO-8601 strings or epoch milliseconds (what Android gives us)."""
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    return v


class NotificationIn(BaseModel):
    """Payload posted by logger-apk. See logger-apk/README.md for the contract."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    instance_id: str = Field(alias="instance", min_length=1)
    package: str
    posted_at: datetime
    channel_id: str | None = None
    title: str | None = None
    text: str | None = None
    big_text: str | None = None
    ongoing: bool = False
    extras: dict[str, Any] = Field(default_factory=dict)
    notification_key: str | None = None
    action: str = "posted"
    screenshot_path: str | None = None
    logger_version: str | None = None

    _dt = field_validator("posted_at", mode="before")(_to_dt)

    @field_validator("posted_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


class SessionStart(BaseModel):
    instance_id: str
    script: str
    started_at: datetime | None = None


class SessionEnd(BaseModel):
    ended_at: datetime | None = None
    screens: list[dict[str, Any]] = Field(default_factory=list)
