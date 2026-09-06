from __future__ import annotations

from pydantic import BaseModel, Field


class ImportErrorRecord(BaseModel):
    row: int
    message: str


class ParticipantImportResult(BaseModel):
    created_count: int = Field(alias="createdCount")
    skipped_count: int = Field(alias="skippedCount")
    errors: list[ImportErrorRecord]
