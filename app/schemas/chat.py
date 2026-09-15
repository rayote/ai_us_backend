from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChatSourceType = Literal["link", "text", "file", "image"]
ChatSubmissionPoint = Literal["afterRound1", "afterRound4"]
ParseStatus = Literal["parsed", "placeholder", "warning"]
ChatSubmissionStatus = Literal["active", "deletion_requested"]


class TranscriptMessage(BaseModel):
    speaker: Literal["user", "assistant", "unknown"]
    text: str


class ParsedTranscript(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: ParseStatus
    parser_version: str = Field(alias="parserVersion")
    messages: list[TranscriptMessage]
    plain_text: str = Field(alias="plainText")
    warnings: list[str]


class ChatSubmissionCreate(BaseModel):
    submission_point: ChatSubmissionPoint = Field(alias="submissionPoint")
    source_type: ChatSourceType = Field(alias="sourceType")
    raw_input: str = Field(alias="rawInput", min_length=1, max_length=200_000)
    submission_id: str = Field(alias="submissionId", min_length=1, max_length=128)


class ChatSubmissionAccepted(BaseModel):
    submission_id: str = Field(alias="submissionId")
    status: str


class ChatAttachment(BaseModel):
    file_id: str = Field(alias="fileId")
    filename: str
    content_type: str = Field(alias="contentType")
    size: int


class ChatSubmissionRecord(BaseModel):
    submission_id: str | None = Field(default=None, alias="submissionId")
    participant_id: str = Field(alias="participantId")
    submission_point: ChatSubmissionPoint = Field(alias="submissionPoint")
    source_type: ChatSourceType = Field(alias="sourceType")
    tool: str | None = None
    raw_input: str = Field(alias="rawInput")
    transcript: ParsedTranscript
    submitted_at: datetime = Field(alias="submittedAt")
    attachments: list[ChatAttachment] = Field(default_factory=list)
    status: ChatSubmissionStatus = "active"
    deletion_requested_at: datetime | None = Field(default=None, alias="deletionRequestedAt")


class ChatSubmissionSummary(BaseModel):
    submission_id: str = Field(alias="submissionId")
    participant_id: str | None = Field(default=None, alias="participantId")
    submission_point: ChatSubmissionPoint = Field(alias="submissionPoint")
    source_type: ChatSourceType = Field(alias="sourceType")
    tool: str | None = None
    filenames: list[str]
    submitted_at: datetime = Field(alias="submittedAt")
    status: ChatSubmissionStatus


class ChatSubmissionDeleted(BaseModel):
    status: Literal["deleted"]
