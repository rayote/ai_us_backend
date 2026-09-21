from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any, Protocol

from app.schemas.chat import TranscriptParseRun
from app.services.chats import ChatSubmissionRepository, ChatUploadRepository
from app.services.transcript_adapters import UnsupportedTranscriptError, normalize_export
from bson import ObjectId


class TranscriptParseRunRepository(Protocol):
    async def create(self, submission_id: str, parser_name: str, parser_version: str) -> TranscriptParseRun:
        ...

    async def get(self, run_id: str) -> TranscriptParseRun | None:
        ...

    async def latest(self, submission_id: str) -> TranscriptParseRun | None:
        ...

    async def complete(self, run_id: str, parser_name: str, parser_version: str, normalized_json: dict[str, Any], warnings: list[str]) -> None:
        ...

    async def fail(self, run_id: str, error: str, warnings: list[str]) -> None:
        ...


def _run(document: dict[str, Any]) -> TranscriptParseRun:
    return TranscriptParseRun(runId=str(document["_id"]), submissionId=document["submission_id"], status=document["status"], parserName=document["parser_name"], parserVersion=document["parser_version"], schemaVersion=document["schema_version"], normalizedJson=document.get("normalized_json"), warnings=document.get("warnings", []), createdAt=document["created_at"], completedAt=document.get("completed_at"), error=document.get("error"))


class MongoTranscriptParseRunRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, submission_id: str, parser_name: str, parser_version: str) -> TranscriptParseRun:
        document = {"submission_id": submission_id, "status": "queued", "parser_name": parser_name, "parser_version": parser_version, "schema_version": "transcript-v1", "warnings": [], "created_at": datetime.now(UTC), "completed_at": None, "error": None}
        result = await self._collection.insert_one(document)
        document["_id"] = result.inserted_id
        return _run(document)

    async def get(self, run_id: str) -> TranscriptParseRun | None:
        document = await self._collection.find_one({"_id": ObjectId(run_id)}) if ObjectId.is_valid(run_id) else None
        return _run(document) if document else None

    async def latest(self, submission_id: str) -> TranscriptParseRun | None:
        document = await self._collection.find_one({"submission_id": submission_id, "status": "completed"}, sort=[("completed_at", -1)])
        return _run(document) if document else None

    async def complete(self, run_id: str, parser_name: str, parser_version: str, normalized_json: dict[str, Any], warnings: list[str]) -> None:
        await self._collection.update_one({"_id": ObjectId(run_id)}, {"$set": {"status": "completed", "parser_name": parser_name, "parser_version": parser_version, "normalized_json": normalized_json, "warnings": warnings, "completed_at": datetime.now(UTC), "error": None}})

    async def fail(self, run_id: str, error: str, warnings: list[str]) -> None:
        await self._collection.update_one({"_id": ObjectId(run_id)}, {"$set": {"status": "failed", "warnings": warnings, "completed_at": datetime.now(UTC), "error": error[:1000]}})


async def process_transcript_parse(payload: dict[str, object], submissions: ChatSubmissionRepository, uploads: ChatUploadRepository, runs: TranscriptParseRunRepository) -> None:
    submission_id, run_id = str(payload["submissionId"]), str(payload["runId"])
    submission = next((item for item in await submissions.list_submissions() if item.submission_id == submission_id), None)
    if submission is None:
        raise ValueError("대화문 제출을 찾을 수 없습니다.")
    try:
        if submission.attachments:
            attachment = submission.attachments[0]
            filename, content, _ = await uploads.read_bytes(attachment.file_id)
            parser_name, parser_version, normalized, warnings = normalize_export(submission.tool, filename, content, submission.participant_id)
        else:
            normalized = {"participantId": submission.participant_id, "platform": submission.tool or "text", "sourceType": submission.source_type, "schemaVersion": "transcript-v1", "parsedAt": datetime.now(UTC).isoformat(), "summary": {"totalSessions": 1, "totalTurns": len(submission.transcript.messages)}, "sessions": [{"sessionId": f"text_{submission_id}", "sessionMetadata": {"totalTurns": len(submission.transcript.messages)}, "turns": [{"turnId": index + 1, "role": message.speaker, "content": message.text, "timestamp": None} for index, message in enumerate(submission.transcript.messages)]}]}
            parser_name, parser_version, warnings = "text", submission.transcript.parser_version, submission.transcript.warnings
        await runs.complete(run_id, parser_name, parser_version, normalized, warnings)
    except UnsupportedTranscriptError as error:
        await runs.complete(run_id, "unsupported", "unsupported-v1", {"participantId": submission.participant_id, "schemaVersion": "transcript-v1", "sessions": []}, [str(error)])
    except Exception as error:
        await runs.fail(run_id, str(error), [])
        raise


def normalized_to_csv(normalized: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["participantId", "platform", "sessionId", "sessionTitle", "sessionStart", "sessionEnd", "sessionDurationSeconds", "turnId", "role", "timestamp", "content", "errors"])
    for session in normalized.get("sessions", []):
        meta = session.get("sessionMetadata", {})
        for turn in session.get("turns", []):
            writer.writerow([normalized.get("participantId", ""), normalized.get("platform", ""), session.get("sessionId", ""), session.get("sessionTitle", ""), meta.get("startTime", ""), meta.get("endTime", ""), meta.get("durationSeconds", ""), turn.get("turnId", ""), turn.get("role", ""), turn.get("timestamp", ""), turn.get("content", ""), (turn.get("turnMetadata") or {}).get("errors", "")])
    return output.getvalue()
