from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobType = Literal["survey_response", "chat_submission", "password_reset_email"]
JobStatus = Literal["queued", "processing", "completed", "failed"]


class JobCreate(BaseModel):
    job_type: JobType
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]


class Job(BaseModel):
    id: str
    job_type: JobType
    idempotency_key: str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    created_at: datetime
    processed_at: datetime | None = None
    error: str | None = None
