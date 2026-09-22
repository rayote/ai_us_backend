from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from gridfs import GridFSBucket
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CONFIRMATION = "IMPORT-CHAT-UPLOAD"
DEFAULT_MAX_FILE_BYTES = 200 * 1024 * 1024
GEMINI_TOOL_TAG = "gemini"


def normalize_phone(value: str) -> str:
    normalized = re.sub(r"\D", "", value)
    if not re.fullmatch(r"01\d{9}", normalized):
        raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
    return normalized


def inspect_gemini_zip(source: Path, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES) -> dict[str, Any]:
    if not source.is_file():
        raise ValueError(f"파일을 찾을 수 없습니다: {source}")
    if source.suffix.lower() != ".zip" or not zipfile.is_zipfile(source):
        raise ValueError("유효한 ZIP 파일이 아닙니다.")
    size = source.stat().st_size
    if size > max_file_bytes:
        raise ValueError(f"관리자 주입 한도 {max_file_bytes // (1024 * 1024)}MB를 초과했습니다.")

    with zipfile.ZipFile(source) as archive:
        candidates = [
            member
            for member in archive.infolist()
            if not member.is_dir()
            if member.filename.lower().endswith((".html", ".htm"))
            if "gemini" in member.filename.lower()
        ]
        preferred = next(
            (
                member
                for member in candidates
                if PurePosixPath(member.filename).name.lower() in {"myactivity.html", "내활동.html"}
            ),
            candidates[0] if candidates else None,
        )
        if preferred is None:
            raise ValueError("ZIP에서 Gemini 내 활동 HTML 파일을 찾지 못했습니다.")
        digest = hashlib.sha256()
        with source.open("rb") as input_file:
            while chunk := input_file.read(1024 * 1024):
                digest.update(chunk)
        return {
            "archiveBytes": size,
            "archiveSha256": digest.hexdigest(),
            "memberCount": len(archive.infolist()),
            "geminiActivityFile": preferred.filename,
            "geminiActivityBytes": preferred.file_size,
        }


def import_submission(
    database: Any,
    bucket: Any,
    source: Path,
    participant_id: str,
    submission_point: str,
    tool: str,
    submission_id: str,
    archive_sha256: str,
) -> tuple[str, str]:
    metadata = {
        "participant_id": participant_id,
        "submission_point": submission_point,
        "source_type": "file",
        "tool": tool,
        "content_type": "application/zip",
        "client_submission_id": submission_id,
        "import_sha256": archive_sha256,
        "uploaded_by": "researcher-script",
    }
    file_id = None
    inserted_id = None
    try:
        with source.open("rb") as input_file:
            file_id = bucket.upload_from_stream(source.name, input_file, metadata=metadata)
        document = {
            "client_submission_id": submission_id,
            "participant_id": participant_id,
            "submission_point": submission_point,
            "source_type": "file",
            "tool": tool,
            "raw_input": source.name,
            "transcript": {
                "status": "placeholder",
                "parser_version": "attachment-v1",
                "messages": [],
                "plain_text": "",
                "warnings": ["원본 첨부 파일은 GridFS에 보관됩니다. 대화문 추출은 아직 수행되지 않았습니다."],
            },
            "submitted_at": datetime.now(UTC),
            "attachments": [
                {
                    "fileId": str(file_id),
                    "filename": source.name,
                    "contentType": "application/zip",
                    "size": source.stat().st_size,
                }
            ],
            "status": "active",
            "import_sha256": archive_sha256,
            "uploaded_by": "researcher-script",
        }
        result = database.chat_submissions.insert_one(document)
        inserted_id = result.inserted_id
        stored = database.chat_submissions.find_one(
            {"_id": inserted_id, "participant_id": participant_id, "status": "active"}
        )
        if stored is None:
            raise RuntimeError("주입 후 제출 내역 검증에 실패했습니다.")
        return str(inserted_id), str(file_id)
    except Exception:
        if inserted_id is not None:
            database.chat_submissions.delete_one({"_id": inserted_id})
        if file_id is not None:
            bucket.delete(file_id)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import an oversized participant chat ZIP through the normal GridFS submission structure."
    )
    parser.add_argument("--phone", required=True, help="Participant phone number")
    parser.add_argument("--file", required=True, type=Path, help="Server-local Gemini Takeout ZIP path")
    parser.add_argument("--submission-point", required=True, choices=("afterRound1", "afterRound4"))
    parser.add_argument("--submission-id", default=f"researcher-proxy-{uuid.uuid4().hex}")
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--max-file-mb", type=int, default=200)
    parser.add_argument("--apply", action="store_true", help="Upload to GridFS and create the submission record")
    parser.add_argument("--confirm", help=f"Required with --apply: {CONFIRMATION}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"--apply requires --confirm {CONFIRMATION}")
    if args.max_file_mb <= 0:
        raise SystemExit("--max-file-mb must be positive")

    from app.core.settings import Settings

    settings = Settings.from_environment()
    mongodb_uri = args.mongodb_uri or settings.mongodb_uri
    database_name = args.database_name or settings.database_name
    if mongodb_uri is None:
        raise SystemExit("MongoDB connection settings are required.")

    try:
        phone = normalize_phone(args.phone)
        inspection = inspect_gemini_zip(args.file, args.max_file_mb * 1024 * 1024)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    client = MongoClient(mongodb_uri, tz_aware=True)
    database = client[database_name]
    try:
        participants = list(database.participants.find({"phone_normalized": phone, "role": "participant"}).limit(2))
        if not participants:
            raise SystemExit("해당 휴대폰 번호의 참가자를 찾지 못했습니다.")
        if len(participants) != 1:
            raise SystemExit("같은 휴대폰 번호의 참가자가 여러 명입니다. 데이터 확인 후 다시 실행하세요.")
        participant = participants[0]
        if not participant.get("chat_consent", False):
            raise SystemExit("참가자의 AI 대화문 제출 동의가 확인되지 않았습니다.")
        if database.chat_submissions.find_one({"client_submission_id": args.submission_id}) is not None:
            raise SystemExit(f"이미 존재하는 submission ID입니다: {args.submission_id}")

        participant_id = str(participant["_id"])
        duplicate = database.chat_submissions.find_one(
            {
                "participant_id": participant_id,
                "submission_point": args.submission_point,
                "import_sha256": inspection["archiveSha256"],
            }
        )
        if duplicate is not None:
            raise SystemExit(f"같은 ZIP이 이미 주입되었습니다: {duplicate['_id']}")
        plan = {
            "mode": "apply" if args.apply else "dry-run",
            "database": database_name,
            "participantId": participant_id,
            "submissionPoint": args.submission_point,
            "tool": GEMINI_TOOL_TAG,
            "submissionId": args.submission_id,
            "filename": args.file.name,
            **inspection,
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if not args.apply:
            print(f"Dry-run only. Re-run with --apply --confirm {CONFIRMATION} to import.")
            return 0

        bucket = GridFSBucket(database, bucket_name="chat_uploads")
        submission_object_id, file_id = import_submission(
            database,
            bucket,
            args.file,
            participant_id,
            args.submission_point,
            GEMINI_TOOL_TAG,
            args.submission_id,
            str(inspection["archiveSha256"]),
        )
        print(
            json.dumps(
                {
                    "status": "imported",
                    "submissionObjectId": submission_object_id,
                    "gridFsFileId": str(file_id),
                    "visibleInParticipantHistory": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
