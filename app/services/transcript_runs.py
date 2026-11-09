from __future__ import annotations

import csv
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.db.json_storage import decode_json_storage, encode_json_storage
from app.schemas.chat import ChatSubmissionRecord, ParsedTranscript, TranscriptMessage, TranscriptParseRun
from app.services.auth import ParticipantAccountRepository
from app.services.chats import ChatSubmissionRepository, ChatUploadRepository
from app.services.screenshot_transcripts import (
    ScreenshotImage,
    ScreenshotTranscriptExtractor,
    normalize_screenshot_extraction,
    order_screenshot_images,
)
from app.services.transcript_adapters import InvalidTranscriptSourceError, UnsupportedTranscriptError, normalize_export
from bson import ObjectId


def source_error_reason(code: str | None) -> str:
    return {
        "missing_required_files": "필수 대화 파일 없음",
        "wrong_service_export": "다른 서비스 export",
        "invalid_archive": "ZIP 손상 · 재내보내기 필요",
    }.get(code, "대화 원본 확인 필요")


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

    async def fail(self, run_id: str, error: str, warnings: list[str], source_error_code: str | None = None) -> None: ...


def _run(document: dict[str, Any]) -> TranscriptParseRun:
    return TranscriptParseRun(
        runId=str(document["_id"]),
        submissionId=document["submission_id"],
        status=document["status"],
        parserName=document["parser_name"],
        parserVersion=document["parser_version"],
        schemaVersion=document["schema_version"],
        normalizedJson=decode_json_storage(document.get("normalized_json")),
        warnings=document.get("warnings", []),
        createdAt=document["created_at"],
        completedAt=document.get("completed_at"),
        error=document.get("error"),
        sourceErrorCode=document.get("source_error_code"),
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
                    "normalized_json": encode_json_storage(normalized_json),
                    "warnings": warnings,
                    "completed_at": datetime.now(UTC),
                    "error": None,
                }
            },
        )

    async def fail(self, run_id: str, error: str, warnings: list[str], source_error_code: str | None = None) -> None:
        await self._collection.update_one(
            {"_id": ObjectId(run_id)},
            {
                "$set": {
                    "status": "failed",
                    "warnings": warnings,
                    "completed_at": datetime.now(UTC),
                    "error": error[:1000],
                    "source_error_code": source_error_code,
                }
            },
        )


async def _check_material_consent(
    submission: ChatSubmissionRecord, participants: ParticipantAccountRepository | None,
) -> None:
    if participants is not None:
        account = await participants.find_by_id(submission.participant_id)
        if account is None or not account.allows_chat_material(submission.submitted_at):
            raise ValueError("동의 철회로 자료 이용이 제한되었습니다.")


async def process_transcript_parse(
    payload: dict[str, object],
    submissions: ChatSubmissionRepository,
    uploads: ChatUploadRepository,
    runs: TranscriptParseRunRepository,
    screenshot_extractor: ScreenshotTranscriptExtractor | None = None,
    participants: ParticipantAccountRepository | None = None,
) -> None:
    submission_id, run_id = str(payload["submissionId"]), str(payload["runId"])
    submission = await submissions.get_by_id(submission_id)
    if submission is None or submission.status != "active":
        raise ValueError("대화문 제출을 찾을 수 없습니다.")
    try:
        await _check_material_consent(submission, participants)
        if submission.transcript.status == "invalid_source":
            await runs.fail(
                run_id, source_error_reason(submission.transcript.source_error_code), [],
                source_error_code=submission.transcript.source_error_code,
            )
            return
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
        await _check_material_consent(submission, participants)
        if not await submissions.update_transcript(submission_id, projected):
            raise ValueError("파싱 결과를 대화문 제출에 저장하지 못했습니다.")
        await runs.complete(run_id, parser_name, parser_version, normalized, warnings)
    except InvalidTranscriptSourceError as error:
        reason = source_error_reason(error.code)
        await runs.fail(run_id, reason, [])
        await _check_material_consent(submission, participants)
        previous = await runs.latest(submission_id)
        if previous is not None and previous.normalized_json is not None:
            restored = _project_transcript(previous.normalized_json, previous.parser_version, previous.warnings)
            if not await submissions.update_transcript(submission_id, restored):
                raise ValueError("기존 파싱 결과를 복원하지 못했습니다.")
        elif submission.transcript.status != "parsed":
            invalid = ParsedTranscript(
                status="invalid_source", parserVersion="source-validation-v1",
                messages=[], plainText="", warnings=[reason], sourceErrorCode=error.code,
            )
            if not await submissions.update_transcript(submission_id, invalid):
                raise ValueError("원본 파일 오류 상태를 저장하지 못했습니다.")
            await runs.fail(run_id, reason, [], source_error_code=error.code)
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
    participants: ParticipantAccountRepository | None = None,
) -> tuple[int, int]:
    completed = 0
    failures: list[list[str]] = []
    profiles = participant_profiles or {}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for submission in submissions:
            await _check_material_consent(submission, participants)
            run = await runs.latest(submission.submission_id) if submission.transcript.status == "parsed" else None
            if run is None and submission.transcript.status != "invalid_source":
                run = await runs.create(submission.submission_id, "adapter-router", "adapter-router-v1")
                try:
                    await process_transcript_parse(
                        {"submissionId": submission.submission_id, "runId": run.run_id},
                        submission_repository,
                        uploads,
                        runs,
                        screenshot_extractor,
                        participants,
                    )
                except Exception:
                    pass
                run = await runs.get(run.run_id)
            if run is None or run.status != "completed" or run.normalized_json is None:
                reason = source_error_reason(submission.transcript.source_error_code) if submission.transcript.status == "invalid_source" else (
                    run.error if run and run.error else "지원되는 파싱 결과를 생성하지 못했습니다."
                )
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
            await _check_material_consent(submission, participants)
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
