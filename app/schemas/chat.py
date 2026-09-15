from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ChatSourceType = Literal["link", "text", "file", "image"]
ChatSubmissionPoint = Literal["afterRound1", "afterRound4"]
ParseStatus = Literal["parsed", "placeholder", "warning"]


class TranscriptMessage(BaseModel):
    speaker: Literal["user", "assistant", "unknown"]
    text: str


class ParsedTranscript(BaseModel):
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
    participant_id: str = Field(alias="participantId")
    submission_point: ChatSubmissionPoint = Field(alias="submissionPoint")
    source_type: ChatSourceType = Field(alias="sourceType")
    raw_input: str = Field(alias="rawInput")
    transcript: ParsedTranscript
    submitted_at: datetime = Field(alias="submittedAt")
    attachments: list[ChatAttachment] = Field(default_factory=list)
