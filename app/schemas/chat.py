from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ChatSourceType = Literal["link", "text", "file", "image"]
ChatSubmissionPoint = Literal["afterRound1", "afterRound4"]
ParseStatus = Literal["parsed", "placeholder", "warning"]
ChatSubmissionStatus = Literal["active", "deletion_requested"]
ChatReviewStatus = Literal["incentive_paid", "excluded", "duplicate", "other"]


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


class ChatSubmissionReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: ChatReviewStatus
    note: str | None = Field(default=None, max_length=500)
    updated_at: datetime = Field(alias="updatedAt")
    updated_by: str = Field(alias="updatedBy")


class ChatSubmissionReviewUpdate(BaseModel):
    status: ChatReviewStatus
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_note(self) -> ChatSubmissionReviewUpdate:
        note = self.note.strip() if self.note else None
        if self.status == "other" and not note:
            raise ValueError("기타 사유를 입력해 주세요.")
        self.note = note
        return self


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
    deletion_requested_at: datetime | None = Field(
        default=None, alias="deletionRequestedAt"
    )
    review: ChatSubmissionReview | None = None


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


class ChatSubmissionPreview(BaseModel):
    submission_id: str = Field(alias="submissionId")
    participant_phone: str = Field(alias="participantPhone")
    school_level: str | None = Field(default=None, alias="schoolLevel")
    grade: int | None = None
    submission_point: ChatSubmissionPoint = Field(alias="submissionPoint")
    source_type: ChatSourceType = Field(alias="sourceType")
    tool: str | None = None
    filenames: list[str]
    attachment_count: int = Field(alias="attachmentCount")
    parse_status: ParseStatus = Field(alias="parseStatus")
    submitted_at: datetime = Field(alias="submittedAt")
    review: ChatSubmissionReview | None = None


class ChatDownloadJobCreate(BaseModel):
    submission_ids: list[str] = Field(
        default_factory=list, alias="submissionIds", max_length=1000
    )
    submission_point: ChatSubmissionPoint | None = Field(
        default=None, alias="submissionPoint"
    )
    school_level: Literal["초등", "중등", "고등"] | None = Field(
        default=None, alias="schoolLevel"
    )
    review_filter: Literal[
        "all",
        "unreviewed",
        "reviewed",
        "incentive_paid",
        "excluded",
        "duplicate",
        "other",
    ] = Field(default="unreviewed", alias="reviewFilter")


class ChatDownloadJobAccepted(BaseModel):
    job_id: str = Field(alias="jobId")
    status: str


class ChatDownloadJobStatus(BaseModel):
    job_id: str = Field(alias="jobId")
    status: str
    error: str | None = None
    download_url: str | None = Field(default=None, alias="downloadUrl")


class TranscriptParseRun(BaseModel):
    run_id: str = Field(alias="runId")
    submission_id: str = Field(alias="submissionId")
    status: Literal["queued", "processing", "completed", "warning", "failed"]
    parser_name: str = Field(alias="parserName")
    parser_version: str = Field(alias="parserVersion")
    schema_version: str = Field(alias="schemaVersion")
    normalized_json: dict[str, Any] | None = Field(default=None, alias="normalizedJson")
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(alias="createdAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    error: str | None = None


class TranscriptParseRequestAccepted(BaseModel):
    run_id: str = Field(alias="runId")
    job_id: str = Field(alias="jobId")
    status: str


class TranscriptParsePreview(BaseModel):
    submission_id: str = Field(alias="submissionId")
    latest_run: TranscriptParseRun | None = Field(default=None, alias="latestRun")
