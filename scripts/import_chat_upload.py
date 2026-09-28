from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bson import Binary, ObjectId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.chat_imports import inspect_gemini_zip

CONFIRMATION = "IMPORT-CHAT-UPLOAD"
DEFAULT_MAX_FILE_BYTES = 200 * 1024 * 1024
DEFAULT_CHUNK_SIZE_BYTES = 4 * 1024 * 1024
DEFAULT_RECONNECT_CHUNKS = 16
GEMINI_TOOL_TAG = "gemini"


def normalize_phone(value: str) -> str:
    normalized = re.sub(r"\D", "", value)
    if not re.fullmatch(r"01\d{9}", normalized):
        raise ValueError("휴대폰 번호는 숫자 11자리여야 합니다.")
    return normalized


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
            file_id = bucket.upload_from_stream(
                source.name, input_file, metadata=metadata
            )
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
                "warnings": [
                    "원본 첨부 파일은 GridFS에 보관됩니다. 대화문 추출은 아직 수행되지 않았습니다."
                ],
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


def resumable_file_id(
    participant_id: str, submission_point: str, archive_sha256: str
) -> ObjectId:
    identity = f"{participant_id}:{submission_point}:{archive_sha256}".encode()
    return ObjectId(hashlib.sha256(identity).hexdigest()[:24])


def upload_resumable_gridfs(
    mongodb_uri: str,
    database_name: str,
    source: Path,
    file_id: ObjectId,
    metadata: dict[str, Any],
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    reconnect_chunks: int = DEFAULT_RECONNECT_CHUNKS,
) -> ObjectId:
    expected_length = source.stat().st_size
    total_chunks = (expected_length + chunk_size_bytes - 1) // chunk_size_bytes
    client: MongoClient[Any] | None = None

    client = MongoClient(mongodb_uri, tz_aware=True)
    database = client[database_name]
    existing_file = database["chat_uploads.files"].find_one({"_id": file_id})
    if existing_file is not None:
        stored_chunks = database["chat_uploads.chunks"].count_documents(
            {"files_id": file_id}
        )
        metadata_matches = existing_file.get("metadata", {}).get(
            "import_sha256"
        ) == metadata.get("import_sha256")
        if (
            existing_file.get("length") != expected_length
            or existing_file.get("chunkSize") != chunk_size_bytes
            or stored_chunks != total_chunks
            or not metadata_matches
        ):
            client.close()
            raise RuntimeError("기존 GridFS 파일이 불완전하거나 원본과 다릅니다.")
        client.close()
        print(f"Reusing completed GridFS file with {stored_chunks} chunks.", flush=True)
        return file_id
    client.close()
    client = None

    with source.open("rb") as input_file:
        for chunk_number in range(total_chunks):
            if chunk_number % reconnect_chunks == 0:
                if client is not None:
                    client.close()
                client = MongoClient(mongodb_uri, tz_aware=True)
                database = client[database_name]
                chunks = database["chat_uploads.chunks"]

            input_file.seek(chunk_number * chunk_size_bytes)
            content = input_file.read(chunk_size_bytes)
            existing = chunks.find_one(
                {"files_id": file_id, "n": chunk_number}, {"data": 1}
            )
            if existing is not None:
                if bytes(existing["data"]) != content:
                    client.close()
                    raise RuntimeError(
                        f"기존 GridFS chunk {chunk_number}의 내용이 원본과 다릅니다."
                    )
                continue
            try:
                chunks.insert_one(
                    {"files_id": file_id, "n": chunk_number, "data": Binary(content)}
                )
            except DuplicateKeyError:
                existing = chunks.find_one(
                    {"files_id": file_id, "n": chunk_number}, {"data": 1}
                )
                if existing is None or bytes(existing["data"]) != content:
                    client.close()
                    raise
            if (
                chunk_number + 1
            ) % reconnect_chunks == 0 or chunk_number + 1 == total_chunks:
                print(
                    f"Uploaded {chunk_number + 1}/{total_chunks} GridFS chunks.",
                    flush=True,
                )

    try:
        if client is None:
            raise RuntimeError("빈 파일은 GridFS에 업로드할 수 없습니다.")
        database = client[database_name]
        chunks = database["chat_uploads.chunks"]
        stored_chunks = chunks.count_documents({"files_id": file_id})
        if stored_chunks != total_chunks:
            raise RuntimeError(
                f"GridFS chunk 검증 실패: {stored_chunks}/{total_chunks}"
            )
        files = database["chat_uploads.files"]
        existing_file = files.find_one({"_id": file_id})
        if existing_file is None:
            files.insert_one(
                {
                    "_id": file_id,
                    "length": expected_length,
                    "chunkSize": chunk_size_bytes,
                    "uploadDate": datetime.now(UTC),
                    "filename": source.name,
                    "metadata": metadata,
                }
            )
        elif (
            existing_file.get("length") != expected_length
            or existing_file.get("chunkSize") != chunk_size_bytes
        ):
            raise RuntimeError("기존 GridFS 파일 metadata가 원본과 다릅니다.")
        return file_id
    finally:
        if client is not None:
            client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import an oversized participant chat ZIP through the normal GridFS submission structure."
    )
    parser.add_argument("--phone", required=True, help="Participant phone number")
    parser.add_argument(
        "--file", required=True, type=Path, help="Server-local Gemini Takeout ZIP path"
    )
    parser.add_argument(
        "--submission-point", required=True, choices=("afterRound1", "afterRound4")
    )
    parser.add_argument(
        "--submission-id", default=f"researcher-proxy-{uuid.uuid4().hex}"
    )
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--max-file-mb", type=int, default=200)
    parser.add_argument("--chunk-size-mb", type=int, default=4)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Upload to GridFS and create the submission record",
    )
    parser.add_argument("--confirm", help=f"Required with --apply: {CONFIRMATION}")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.apply and args.confirm != CONFIRMATION:
        raise SystemExit(f"--apply requires --confirm {CONFIRMATION}")
    if args.max_file_mb <= 0:
        raise SystemExit("--max-file-mb must be positive")
    if args.chunk_size_mb <= 0 or args.chunk_size_mb >= 16:
        raise SystemExit("--chunk-size-mb must be between 1 and 15")

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
        participants = list(
            database.participants.find(
                {"phone_normalized": phone, "role": "participant"}
            ).limit(2)
        )
        if not participants:
            raise SystemExit("해당 휴대폰 번호의 참가자를 찾지 못했습니다.")
        if len(participants) != 1:
            raise SystemExit(
                "같은 휴대폰 번호의 참가자가 여러 명입니다. 데이터 확인 후 다시 실행하세요."
            )
        participant = participants[0]
        if not participant.get("chat_consent", False):
            raise SystemExit("참가자의 AI 대화문 제출 동의가 확인되지 않았습니다.")
        if (
            database.chat_submissions.find_one(
                {"client_submission_id": args.submission_id}
            )
            is not None
        ):
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
            "chunkSizeBytes": args.chunk_size_mb * 1024 * 1024,
            **inspection,
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if not args.apply:
            print(
                f"Dry-run only. Re-run with --apply --confirm {CONFIRMATION} to import."
            )
            return 0

        metadata = {
            "participant_id": participant_id,
            "submission_point": args.submission_point,
            "source_type": "file",
            "tool": GEMINI_TOOL_TAG,
            "content_type": "application/zip",
            "client_submission_id": args.submission_id,
            "import_sha256": str(inspection["archiveSha256"]),
            "uploaded_by": "researcher-script",
        }
        file_id = upload_resumable_gridfs(
            mongodb_uri,
            database_name,
            args.file,
            resumable_file_id(
                participant_id, args.submission_point, str(inspection["archiveSha256"])
            ),
            metadata,
            args.chunk_size_mb * 1024 * 1024,
        )
        document = {
            "client_submission_id": args.submission_id,
            "participant_id": participant_id,
            "submission_point": args.submission_point,
            "source_type": "file",
            "tool": GEMINI_TOOL_TAG,
            "raw_input": args.file.name,
            "transcript": {
                "status": "placeholder",
                "parser_version": "attachment-v1",
                "messages": [],
                "plain_text": "",
                "warnings": [
                    "원본 첨부 파일은 GridFS에 보관됩니다. 대화문 추출은 아직 수행되지 않았습니다."
                ],
            },
            "submitted_at": datetime.now(UTC),
            "attachments": [
                {
                    "fileId": str(file_id),
                    "filename": args.file.name,
                    "contentType": "application/zip",
                    "size": args.file.stat().st_size,
                }
            ],
            "status": "active",
            "import_sha256": str(inspection["archiveSha256"]),
            "uploaded_by": "researcher-script",
        }
        submission_object_id = database.chat_submissions.insert_one(
            document
        ).inserted_id
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
