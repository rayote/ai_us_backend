from __future__ import annotations

import argparse
import asyncio
import fcntl
import io
import json
import os
import sys
import tempfile
import zipfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bson import ObjectId
from dotenv import dotenv_values
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.mongodb import AsyncCollection, AsyncGridFSBucket
from app.services.auth import MongoParticipantAccountRepository, ParticipantAccount
from app.services.chats import MongoChatSubmissionRepository
from app.services.gemini_screenshot_extractor import GeminiScreenshotTranscriptExtractor
from app.services.transcript_adapters import (
    find_chatgpt_export_members,
    find_claude_export_member,
    find_gemini_export_member,
    find_grok_export_member,
)
from app.services.transcript_runs import (
    MongoTranscriptParseRunRepository,
    process_transcript_parse,
)

CONFIRMATION = "PARSE-CHAT-BACKLOG"


def skip_reason(
    submission: dict[str, Any],
    account: ParticipantAccount | None,
    runs: list[dict[str, Any]],
    *,
    retry_incomplete: bool,
    include_images: bool,
) -> str | None:
    if submission.get("status", "active") != "active":
        return "inactive"
    if account is None or not account.allows_chat_material(submission["submitted_at"]):
        return "consent_restricted"
    if submission.get("transcript", {}).get("status") == "parsed" or any(
        run["status"] == "completed" for run in runs
    ):
        return "already_completed"
    if any(run["status"] in {"queued", "processing"} for run in runs):
        return "active_run"
    if runs and not retry_incomplete:
        return "previously_requested"
    if submission.get("source_type") == "image" and not include_images:
        return "image_opt_in_required"
    return None


class TargetSubmissions(MongoChatSubmissionRepository):
    def __init__(self, collection: Any, submission: Any) -> None:
        super().__init__(collection)
        self.submission = submission

    async def list_submissions(self, *args: Any, **kwargs: Any) -> list[Any]:
        return [self.submission]


class FileTranscriptUploads:
    def __init__(self, uploads: Any, tool: str | None) -> None:
        self.uploads = uploads
        self.tool = (tool or "").lower()

    async def read_bytes(self, file_id: str) -> tuple[str, bytes, dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="platform-transcript-") as directory:
            source = Path(directory) / "upload"
            details = await self.uploads.download_to_path(file_id, source)
            selectors = {
                "claude": find_claude_export_member,
                "gemini": find_gemini_export_member,
                "grok": find_grok_export_member,
            }
            if zipfile.is_zipfile(source) and self.tool in {*selectors, "chatgpt"}:
                with zipfile.ZipFile(source) as archive:
                    names = (
                        find_chatgpt_export_members(archive) if self.tool == "chatgpt"
                        else [selectors[self.tool](archive)]
                    )
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as reduced:
                        for name in names:
                            reduced.writestr(name, archive.read(name))
                    content = buffer.getvalue()
            else:
                content = source.read_bytes()
            return details["filename"], content, details.get("metadata", {})


def account_from_metadata(document: dict[str, Any] | None) -> ParticipantAccount | None:
    if document is None:
        return None
    return ParticipantAccount(
        participant_id=str(document["_id"]),
        phone="",
        password_hash="",
        must_change_password=False,
        chat_consent=document.get("chat_consent", False),
        chat_restricted_before=document.get("chat_restricted_before"),
    )


def stale_runs_recoverable(
    runs: list[dict[str, Any]], active_jobs: bool, now: datetime
) -> bool:
    active = [run for run in runs if run["status"] in {"queued", "processing"}]
    return bool(active) and not active_jobs and all(
        run["created_at"].replace(tzinfo=UTC) < now - timedelta(hours=1)
        for run in active
    )


async def execute(args: argparse.Namespace, database: Any, report: Any) -> dict[str, int]:
    accounts = MongoParticipantAccountRepository(AsyncCollection(database.participants))
    runs_repository = MongoTranscriptParseRunRepository(
        AsyncCollection(database.transcript_parse_runs)
    )
    uploads = AsyncGridFSBucket(database, "chat_uploads")
    extractor = None
    if args.include_images:
        image_config = dotenv_values(args.image_env_file)
        key = image_config.get("GEMINI_API_KEY")
        if not key:
            raise ValueError("Image parsing requires GEMINI_API_KEY in --image-env-file")
        extractor = GeminiScreenshotTranscriptExtractor(
            key, image_config.get("GEMINI_SCREENSHOT_MODEL") or "gemini-3.8-flash"
        )
    counts: Counter[str] = Counter()
    filters: dict[str, Any] = {"$or": [{"status": "active"}, {"status": {"$exists": False}}]}
    if args.submission_id:
        filters["_id"] = {"$in": [ObjectId(value) for value in args.submission_id]}
    projection = {
        "participant_id": 1, "submitted_at": 1, "status": 1,
        "source_type": 1, "tool": 1, "transcript.status": 1,
    }
    candidates = list(database.chat_submissions.find(filters, projection).sort("_id", 1))
    for metadata in candidates:
        submission_id = str(metadata["_id"])
        account = account_from_metadata(database.participants.find_one(
            {"_id": ObjectId(metadata["participant_id"]), "role": "participant"},
            {"chat_consent": 1, "chat_restricted_before": 1},
        ))
        runs = list(database.transcript_parse_runs.find(
            {"submission_id": submission_id},
            {"status": 1, "created_at": 1},
        ))
        active_jobs = bool(database.submission_jobs.find_one(
            {"job_type": "transcript_parse", "payload.submissionId": submission_id,
             "status": {"$in": ["queued", "processing"]}}, {"_id": 1},
        ))
        recover = args.recover_stale and stale_runs_recoverable(
            runs, active_jobs, datetime.now(UTC)
        )
        selection_runs = [run for run in runs if run["status"] not in {"queued", "processing"}] if recover else runs
        reason = skip_reason(
            metadata, account, selection_runs,
            retry_incomplete=args.retry_incomplete, include_images=args.include_images,
        )
        if active_jobs:
            reason = "active_job"
        event: dict[str, Any] = {
            "submissionId": submission_id, "tool": metadata.get("tool"),
            "sourceType": metadata.get("source_type"), "status": reason or "eligible",
        }
        selected_count = counts["attempted"] + counts["eligible"]
        if reason is None and (not args.max_items or selected_count < args.max_items):
            if not args.apply:
                counts["eligible"] += 1
            else:
                if recover:
                    for old_run in runs:
                        if old_run["status"] in {"queued", "processing"}:
                            await runs_repository.fail(
                                str(old_run["_id"]),
                                "Operator recovery: no active queue job; run older than one hour.", [],
                            )
                repository = MongoChatSubmissionRepository(AsyncCollection(database.chat_submissions))
                records = await repository.list_for_participant(metadata["participant_id"])
                submission = next(item for item in records if item.submission_id == submission_id)
                run = await runs_repository.create(submission_id, "adapter-router", "adapter-router-v1")
                database.transcript_parse_runs.update_one(
                    {"_id": ObjectId(run.run_id)},
                    {"$set": {"execution_source": "operator-backlog-script"}},
                )
                counts["attempted"] += 1
                try:
                    await process_transcript_parse(
                        {"submissionId": submission_id, "runId": run.run_id},
                        TargetSubmissions(AsyncCollection(database.chat_submissions), submission),
                        FileTranscriptUploads(uploads, submission.tool) if submission.source_type == "file" else uploads,
                        runs_repository, extractor, accounts,
                    )
                except Exception as error:
                    event["exceptionType"] = type(error).__name__
                saved = database.transcript_parse_runs.find_one(
                    {"_id": ObjectId(run.run_id)},
                    {"status": 1, "normalized_json.summary": 1, "parser_name": 1},
                )
                if saved["status"] in {"queued", "processing"}:
                    await runs_repository.fail(
                        run.run_id,
                        "Operator parsing did not reach a terminal state: "
                        + event.get("exceptionType", "unknown"), [],
                    )
                    saved["status"] = "failed"
                event.update(
                    status=saved["status"], runId=run.run_id,
                    parser=saved.get("parser_name"),
                    summary=saved.get("normalized_json", {}).get("summary"),
                )
                counts[event["status"]] += 1
        elif reason is None:
            event["status"] = "batch_limit"
            counts["batch_limit"] += 1
        else:
            counts[reason] += 1
        report.write(json.dumps(event, ensure_ascii=False) + "\n")
        report.flush()
        if event["status"] in {"completed", "warning", "failed"}:
            print(json.dumps(event, ensure_ascii=False), flush=True)
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Locally parse an authorized production chat backlog; dry-run by default.")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--retry-incomplete", action="store_true")
    parser.add_argument("--recover-stale", action="store_true")
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--image-env-file", type=Path)
    parser.add_argument("--submission-id", action="append")
    parser.add_argument("--max-items", type=int, default=0)
    args = parser.parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        parser.error(f"--apply requires --confirm {CONFIRMATION}")
    if args.max_items < 0 or (args.include_images and not args.image_env_file):
        parser.error("Invalid limit or missing --image-env-file")
    if args.recover_stale and not args.retry_incomplete:
        parser.error("--recover-stale requires --retry-incomplete")
    if args.report.resolve().is_relative_to(PROJECT_ROOT):
        parser.error("Reports must be outside the backend repository")
    config = dotenv_values(args.env_file)
    if config.get("AI_US_PRODUCTION_DATABASE_NAME") != "ai_us_production":
        parser.error("Config must explicitly name ai_us_production")
    uri = config.get("AI_US_PRODUCTION_MONGODB_URI")
    if not uri:
        parser.error("Missing explicit production MongoDB URI")
    args.report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = args.report.parent / ".platform-transcript-backlog.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as report:
            client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000, socketTimeoutMS=60000, appname="operator-transcript-backlog")
            try:
                counts = asyncio.run(execute(args, client["ai_us_production"], report))
                print(json.dumps({"mode": "apply" if args.apply else "dry-run", "counts": counts}, ensure_ascii=False), flush=True)
            finally:
                client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())