from __future__ import annotations

import csv
import io
from typing import Any, Protocol

from app.schemas.chat import ChatSubmissionCreate, ChatSubmissionRecord
from app.schemas.jobs import Job, JobCreate
from app.services.auth import ParticipantAccountRepository
from app.services.jobs import JobRepository
from app.services.transcripts import parse_transcript


class ChatConsentRequiredError(Exception):
    """Raised when a participant has not consented to submit AI chats."""


class InvalidChatLinkError(Exception):
    """Raised when a link submission does not contain a valid URL."""


class ChatSubmissionRepository(Protocol):
    async def create_submission(self, submission: ChatSubmissionRecord) -> None: ...

    async def list_submissions(self, submission_point: str | None = None) -> list[ChatSubmissionRecord]: ...


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


def chat_submissions_to_csv(submissions: list[ChatSubmissionRecord]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "participantId",
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
        writer.writerow(
            [
                submission.participant_id,
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
