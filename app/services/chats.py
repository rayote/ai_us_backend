from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.schemas.chat import ChatAttachment, ChatSubmissionCreate, ChatSubmissionRecord, ParsedTranscript
from app.schemas.jobs import Job, JobCreate
from app.services.auth import ParticipantAccountRepository
from app.services.jobs import JobRepository
from app.services.transcripts import parse_transcript


class ChatConsentRequiredError(Exception):
    """Raised when a participant has not consented to submit AI chats."""


class InvalidChatLinkError(Exception):
    """Raised when a link submission does not contain a valid URL."""


class InvalidChatUploadError(Exception):
    """Raised when uploaded chat files do not meet the submission rules."""


class ChatSubmissionRepository(Protocol):
    async def create_submission(self, submission: ChatSubmissionRecord) -> None: ...

    async def list_submissions(self, submission_point: str | None = None) -> list[ChatSubmissionRecord]: ...


class ChatUploadRepository(Protocol):
    async def upload(self, filename: str, data: bytes, metadata: dict[str, Any]) -> str: ...

    async def delete(self, file_id: str) -> None: ...


@dataclass(frozen=True)
class ChatUploadFile:
    filename: str
    content_type: str
    data: bytes


class MongoChatSubmissionRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create_submission(self, submission: ChatSubmissionRecord) -> None:
        await self._collection.insert_one(
            {
                "participant_id": submission.participant_id,
                "submission_point": submission.submission_point,
                "source_type": submission.source_type,
                "raw_input": submission.raw_input,
                "transcript": submission.transcript.model_dump(),
                "submitted_at": submission.submitted_at,
                "attachments": [attachment.model_dump(by_alias=True) for attachment in submission.attachments],
            }
        )

    async def list_submissions(self, submission_point: str | None = None) -> list[ChatSubmissionRecord]:
        filters = {"submission_point": submission_point} if submission_point else {}
        cursor = self._collection.find(filters).sort("submitted_at", 1)
        return [
            ChatSubmissionRecord(
                participantId=str(document["participant_id"]),
                submissionPoint=document["submission_point"],
                sourceType=document["source_type"],
                rawInput=document["raw_input"],
                transcript=document["transcript"],
                submittedAt=document["submitted_at"],
                attachments=document.get("attachments", []),
            )
            async for document in cursor
        ]


class ChatSubmissionService:
    def __init__(
        self,
        participants: ParticipantAccountRepository,
        jobs: JobRepository,
    ) -> None:
        self._participants = participants
        self._jobs = jobs

    async def submit(self, participant_id: str, submission: ChatSubmissionCreate) -> Job:
        participant = await self._participants.find_by_id(participant_id)
        if participant is None or not participant.chat_consent:
            raise ChatConsentRequiredError
        if submission.source_type == "link" and parse_transcript("link", submission.raw_input).status == "warning":
            raise InvalidChatLinkError
        return await self._jobs.enqueue(
            JobCreate(
                job_type="chat_submission",
                idempotency_key=f"{participant_id}:{submission.submission_id}",
                payload={
                    "participantId": participant_id,
                    "submissionPoint": submission.submission_point,
                    "sourceType": submission.source_type,
                    "rawInput": submission.raw_input,
                },
            )
        )


class ChatUploadService:
    _MAX_FILE_BYTES = 25 * 1024 * 1024
    _MAX_TOTAL_BYTES = 100 * 1024 * 1024
    _MAX_IMAGE_FILES = 20
    _IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}

    def __init__(
        self,
        participants: ParticipantAccountRepository,
        submissions: ChatSubmissionRepository,
        uploads: ChatUploadRepository,
    ) -> None:
        self._participants = participants
        self._submissions = submissions
        self._uploads = uploads

    async def submit(
        self,
        participant_id: str,
        submission_point: str,
        source_type: str,
        tool: str,
        files: list[ChatUploadFile],
    ) -> None:
        participant = await self._participants.find_by_id(participant_id)
        if participant is None or not participant.chat_consent:
            raise ChatConsentRequiredError
        self._validate(source_type, files)

        attachments: list[ChatAttachment] = []
        try:
            for file in files:
                filename = Path(file.filename).name
                file_id = await self._uploads.upload(
                    filename,
                    file.data,
                    {
                        "participant_id": participant_id,
                        "submission_point": submission_point,
                        "source_type": source_type,
                        "tool": tool,
                        "content_type": file.content_type,
                    },
                )
                attachments.append(
                    ChatAttachment(
                        fileId=file_id, filename=filename, contentType=file.content_type, size=len(file.data)
                    )
                )
            await self._submissions.create_submission(
                ChatSubmissionRecord(
                    participantId=participant_id,
                    submissionPoint=submission_point,
                    sourceType=source_type,
                    rawInput=", ".join(attachment.filename for attachment in attachments),
                    transcript=ParsedTranscript(
                        status="placeholder",
                        parserVersion="attachment-v1",
                        messages=[],
                        plainText="",
                        warnings=["원본 첨부 파일은 GridFS에 보관됩니다. 대화문 추출은 아직 수행되지 않았습니다."],
                    ),
                    submittedAt=datetime.now(UTC),
                    attachments=attachments,
                )
            )
        except Exception:
            for attachment in attachments:
                await self._uploads.delete(attachment.file_id)
            raise

    def _validate(self, source_type: str, files: list[ChatUploadFile]) -> None:
        if source_type not in {"file", "image"}:
            raise InvalidChatUploadError("첨부 파일 형식이 올바르지 않습니다.")
        if not files:
            raise InvalidChatUploadError("제출할 파일을 선택해 주세요.")
        if source_type == "file" and len(files) != 1:
            raise InvalidChatUploadError("ZIP 파일은 한 개만 제출할 수 있습니다.")
        if source_type == "image" and len(files) > self._MAX_IMAGE_FILES:
            raise InvalidChatUploadError(f"이미지는 최대 {self._MAX_IMAGE_FILES}개까지 제출할 수 있습니다.")
        total_bytes = sum(len(file.data) for file in files)
        if any(len(file.data) > self._MAX_FILE_BYTES for file in files):
            raise InvalidChatUploadError("파일 하나의 크기는 25MB를 넘을 수 없습니다.")
        if total_bytes > self._MAX_TOTAL_BYTES:
            raise InvalidChatUploadError("한 번에 올리는 파일의 총 크기는 100MB를 넘을 수 없습니다.")
        if source_type == "file":
            file = files[0]
            if Path(file.filename).suffix.lower() != ".zip" or not file.data.startswith(b"PK"):
                raise InvalidChatUploadError("유효한 ZIP 파일만 제출할 수 있습니다.")
        elif any(
            Path(file.filename).suffix.lower() not in self._IMAGE_SUFFIXES
            or not file.content_type.startswith("image/")
            for file in files
        ):
            raise InvalidChatUploadError("JPG, PNG, WEBP, HEIC 이미지 파일만 제출할 수 있습니다.")


async def store_chat_submission(payload: dict[str, object], repository: ChatSubmissionRepository) -> None:
    source_type = payload["sourceType"]
    raw_input = payload["rawInput"]
    submission_point = payload["submissionPoint"]
    if not isinstance(source_type, str) or not isinstance(raw_input, str) or not isinstance(submission_point, str):
        raise ValueError("Invalid chat submission job payload")
    from datetime import UTC, datetime

    await repository.create_submission(
        ChatSubmissionRecord(
            participantId=str(payload["participantId"]),
            submissionPoint=submission_point,
            sourceType=source_type,
            rawInput=raw_input,
            transcript=parse_transcript(source_type, raw_input),
            submittedAt=datetime.now(UTC),
        )
    )


def chat_submissions_to_csv(
    submissions: list[ChatSubmissionRecord],
    participant_profiles: dict[str, tuple[str | None, str | None, int | None]] | None = None,
) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "participantId",
            "name",
            "schoolLevel",
            "grade",
            "submissionPoint",
            "sourceType",
            "submittedAt",
            "parseStatus",
            "parserVersion",
            "parseWarnings",
            "plainText",
            "rawInput",
        ]
    )
    for submission in submissions:
        profile = (participant_profiles or {}).get(submission.participant_id, (None, None, None))
        writer.writerow(
            [
                submission.participant_id,
                profile[0] or "",
                profile[1] or "",
                profile[2] or "",
                submission.submission_point,
                submission.source_type,
                submission.submitted_at.isoformat(),
                submission.transcript.status,
                submission.transcript.parser_version,
                "; ".join(submission.transcript.warnings),
                submission.transcript.plain_text,
                submission.raw_input,
            ]
        )
    return output.getvalue()
