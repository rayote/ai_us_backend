from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AnalyticsEventName = Literal[
    "landing_visit",
    "signup_started",
    "signup_submitted",
    "login_success",
    "survey_opened",
    "survey_submitted",
    "survey_heartbeat",
    "chat_submission_created",
]


class AnalyticsEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event: AnalyticsEventName
    visitor_id: str = Field(alias="visitorId", min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    campaign: str | None = Field(default=None, max_length=100)
    utm_source: str | None = Field(default=None, alias="utmSource", max_length=100)
    utm_medium: str | None = Field(default=None, alias="utmMedium", max_length=100)

    @field_validator("campaign", "utm_source", "utm_medium")
    @classmethod
    def normalize_attribution(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AnalyticsEventAccepted(BaseModel):
    status: Literal["tracked"]
