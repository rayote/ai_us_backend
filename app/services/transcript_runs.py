from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.schemas.chat import ParsedTranscript, TranscriptMessage, TranscriptParseRun
from app.services.chats import ChatSubmissionRepository, ChatUploadRepository
from app.services.screenshot_transcripts import (
    ScreenshotImage,
    ScreenshotTranscriptExtractor,
    normalize_screenshot_extraction,
    order_screenshot_images,
)
from app.services.transcript_adapters import UnsupportedTranscriptError, normalize_export
from bson import ObjectId


class TranscriptParseRunRepository(Protocol):
    async def create(self, submission_id: str, parser_name: str, parser_version: str) -> TranscriptParseRun: ...

    async def get(self, run_id: str) -> TranscriptParseRun | None: ...

    async def latest(self, submission_id: str) -> TranscriptParseRun | None: ...

    async def active(self, submission_id: str) -> TranscriptParseRun | None: ...

    async def complete(
        self,
        run_id: str,
        parser_name: str,
        parser_version: str,
        normalized_json: dict[str, Any],
        warnings: list[str],
        status: str = "completed",
    ) -> None: ...

    async def fail(self, run_id: str, error: str, warnings: list[str]) -> None: ...


def _run(document: dict[str, Any]) -> TranscriptParseRun:
    return TranscriptParseRun(
        runId=str(document["_id"]),
        submissionId=document["submission_id"],
        status=document["status"],
        parserName=document["parser_name"],
        parserVersion=document["parser_version"],
        schemaVersion=document["schema_version"],
        normalizedJson=document.get("normalized_json"),
        warnings=document.get("warnings", []),
        createdAt=document["created_at"],
        completedAt=document.get("completed_at"),
        error=document.get("error"),
    )


class MongoTranscriptParseRunRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, submission_id: str, parser_name: str, parser_version: str) -> TranscriptParseRun:
        document = {
            "submission_id": submission_id,
            "status": "queued",
            "parser_name": parser_name,
            "parser_version": parser_version,
            "schema_version": "transcript-v1",
            "warnings": [],
            "created_at": datetime.now(UTC),
            "completed_at": None,
            "error": None,
        }
        result = await self._collection.insert_one(document)
        document["_id"] = result.inserted_id
        return _run(document)

    async def get(self, run_id: str) -> TranscriptParseRun | None:
        document = await self._collection.find_one({"_id": ObjectId(run_id)}) if ObjectId.is_valid(run_id) else None
        return _run(document) if document else None

    async def latest(self, submission_id: str) -> TranscriptParseRun | None:
        document = await self._collection.find_one(
            {"submission_id": submission_id, "status": "completed"}, sort=[("completed_at", -1)]
        )
        return _run(document) if document else None

    async def active(self, submission_id: str) -> TranscriptParseRun | None:
        document = await self._collection.find_one(
            {"submission_id": submission_id, "status": {"$in": ["queued", "processing"]}}, sort=[("created_at", -1)]
        )
        return _run(document) if document else None

    async def complete(
        self,
        run_id: str,
        parser_name: str,
        parser_version: str,
        normalized_json: dict[str, Any],
        warnings: list[str],
        status: str = "completed",
    ) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(run_id)},
            {
                "$set": {
                    "status": status,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                    "normalized_json": normalized_json,
                    "warnings": warnings,
                    "completed_at": datetime.now(UTC),
                    "error": None,
                }
            },
        )

    async def fail(self, run_id: str, error: str, warnings: list[str]) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(run_id)},
            {
                "$set": {
                    "status": "failed",
                    "warnings": warnings,
                    "completed_at": datetime.now(UTC),
                    "error": error[:1000],
                }
            },
        )


async def process_transcript_parse(
    payload: dict[str, object],
    submissions: ChatSubmissionRepository,
    uploads: ChatUploadRepository,
    runs: TranscriptParseRunRepository,
    screenshot_extractor: ScreenshotTranscriptExtractor | None = None,
) -> None:
    submission_id, run_id = str(payload["submissionId"]), str(payload["runId"])
    submission = next(
        (item for item in await submissions.list_submissions() if item.submission_id == submission_id), None
    )
    if submission is None:
        raise ValueError("대화문 제출을 찾을 수 없습니다.")
    try:
        if submission.source_type == "image":
            if screenshot_extractor is None:
                raise UnsupportedTranscriptError("이미지 대화문 parser adapter가 설정되지 않았습니다.")
            images = []
            for attachment in submission.attachments:
                filename, content, metadata = await uploads.read_bytes(attachment.file_id)
                images.append(
                    ScreenshotImage(
                        filename=filename,
                        content_type=str(metadata.get("content_type") or attachment.content_type),
                        data=content,
                    )
                )
            ordered_images = order_screenshot_images(images)
            extracted = await screenshot_extractor.extract(ordered_images, platform=submission.tool)
            normalized, warnings = normalize_screenshot_extraction(
                extracted,
                ordered_images,
                submission.participant_id,
                submission.tool or "other",
            )
            parser_name, parser_version = "screenshot-vlm", "screenshot-vlm-v3"
        elif submission.attachments:
            attachment = submission.attachments[0]
            filename, content, _ = await uploads.read_bytes(attachment.file_id)
            parser_name, parser_version, normalized, warnings = normalize_export(
                submission.tool, filename, content, submission.participant_id
            )
        else:
            normalized = {
                "participantId": submission.participant_id,
                "platform": submission.tool or "text",
                "sourceType": submission.source_type,
                "schemaVersion": "transcript-v1",
                "parsedAt": datetime.now(UTC).isoformat(),
                "summary": {"totalSessions": 1, "totalTurns": len(submission.transcript.messages)},
                "sessions": [
                    {
                        "sessionId": f"text_{submission_id}",
                        "sessionMetadata": {"totalTurns": len(submission.transcript.messages)},
                        "turns": [
                            {"turnId": index + 1, "role": message.speaker, "content": message.text, "timestamp": None}
                            for index, message in enumerate(submission.transcript.messages)
                        ],
                    }
                ],
            }
            parser_name, parser_version, warnings = (
                "text",
                submission.transcript.parser_version,
                submission.transcript.warnings,
            )
        projected = _project_transcript(normalized, parser_version, warnings)
        if not await submissions.update_transcript(submission_id, projected):
            raise ValueError("파싱 결과를 대화문 제출에 저장하지 못했습니다.")
        await runs.complete(run_id, parser_name, parser_version, normalized, warnings)
    except UnsupportedTranscriptError as error:
        warnings = [str(error)]
        normalized = {"participantId": submission.participant_id, "schemaVersion": "transcript-v1", "sessions": []}
        await runs.complete(run_id, "unsupported", "unsupported-v1", normalized, warnings, status="warning")
    except Exception as error:
        await runs.fail(run_id, str(error), [])
        raise


def normalized_to_csv(normalized: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "participantId",
            "platform",
            "sessionId",
            "sessionTitle",
            "sessionStart",
            "sessionEnd",
            "sessionDurationSeconds",
            "turnId",
            "role",
            "speakerLabel",
            "timestamp",
            "content",
            "errors",
            "reviewWarnings",
        ]
    )
    review_warnings = " | ".join(str(warning) for warning in normalized.get("reviewWarnings", []))
    for session in normalized.get("sessions", []):
        meta = session.get("sessionMetadata", {})
        for turn in session.get("turns", []):
            writer.writerow(
                [
                    normalized.get("participantId", ""),
                    normalized.get("platform", ""),
                    session.get("sessionId", ""),
                    session.get("sessionTitle", ""),
                    meta.get("startTime", ""),
                    meta.get("endTime", ""),
                    meta.get("durationSeconds", ""),
                    turn.get("turnId", ""),
                    turn.get("role", ""),
                    (turn.get("turnMetadata") or {}).get("speakerLabel", ""),
                    turn.get("timestamp", ""),
                    turn.get("content", ""),
                    (turn.get("turnMetadata") or {}).get("errors", ""),
                    review_warnings,
                ]
            )
    return output.getvalue()


async def build_transcript_archive(
    submissions: list[Any],
    submission_repository: ChatSubmissionRepository,
    uploads: ChatUploadRepository,
    runs: TranscriptParseRunRepository,
    archive_path: Path,
    participant_profiles: dict[str, tuple[str, str | None, int | None]] | None = None,
    screenshot_extractor: ScreenshotTranscriptExtractor | None = None,
) -> tuple[int, int]:
    completed = 0
    failures: list[list[str]] = []
    profiles = participant_profiles or {}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for submission in submissions:
            run = await runs.latest(submission.submission_id) if submission.transcript.status == "parsed" else None
            if run is None:
                run = await runs.create(submission.submission_id, "adapter-router", "adapter-router-v1")
                try:
                    await process_transcript_parse(
                        {"submissionId": submission.submission_id, "runId": run.run_id},
                        submission_repository,
                        uploads,
                        runs,
                        screenshot_extractor,
                    )
                except Exception:
                    pass
                run = await runs.get(run.run_id)
            if run is None or run.status != "completed" or run.normalized_json is None:
                reason = run.error if run and run.error else "지원되는 파싱 결과를 생성하지 못했습니다."
                if run and run.warnings:
                    reason = " ".join(run.warnings)
                phone, school_level, grade = profiles.get(submission.participant_id, ("", None, None))
                submission_point = {
                    "afterRound1": "대화문 1 (1차 후)",
                    "afterRound4": "대화문 2 (4차 후)",
                }.get(submission.submission_point, submission.submission_point)
                submitted_at = submission.submitted_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y. %m. %d.")
                failures.append(
                    [
                        submission.submission_id,
                        submission.participant_id,
                        submission_point,
                        school_level or "",
                        str(grade) if grade is not None else "",
                        phone,
                        submitted_at,
                        submission.tool or "",
                        reason,
                    ]
                )
                continue
            filename = f"transcript-{submission.submission_id}.csv"
            archive.writestr(filename, ("\ufeff" + normalized_to_csv(run.normalized_json)).encode("utf-8"))
            completed += 1
        if failures:
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            writer.writerow(
                [
                    "submissionId",
                    "participantId",
                    "제출 회차",
                    "학교급",
                    "학년",
                    "휴대폰",
                    "제출일",
                    "platform",
                    "reason",
                ]
            )
            writer.writerows(failures)
            archive.writestr("parse-failures.csv", ("\ufeff" + output.getvalue()).encode("utf-8"))
    return completed, len(failures)


def _project_transcript(normalized: dict[str, Any], parser_version: str, warnings: list[str]) -> ParsedTranscript:
    messages: list[TranscriptMessage] = []
    for session in normalized.get("sessions", []):
        for turn in session.get("turns", []):
            speaker = turn.get("role")
            if speaker not in {"user", "assistant", "unknown"}:
                speaker = "unknown"
            content = str(turn.get("content") or "").strip()
            if content:
                messages.append(TranscriptMessage(speaker=speaker, text=content))
    plain_text = "\n".join(f"{message.speaker}: {message.text}" for message in messages)
    return ParsedTranscript(
        status="parsed" if messages else "warning",
        parserVersion=parser_version,
        messages=messages,
        plainText=plain_text,
        warnings=warnings,
    )
