from __future__ import annotations

import asyncio
import hashlib
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from app.services.transcript_adapters import (
    find_claude_export_member,
    find_grok_export_member,
)
from bson import Binary, ObjectId
from pymongo.errors import DuplicateKeyError

DEFAULT_MAX_FILE_BYTES = 1024 * 1024 * 1024
DEFAULT_CHUNK_SIZE_BYTES = 4 * 1024 * 1024
CHAT_IMPORT_TOOL_TYPES = {
    "chatgpt": "file",
    "gemini": "file",
    "claude": "file",
    "grok": "file",
    "zeta": "image",
    "crack": "image",
    "other": "image",
}
IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
}
MAX_IMAGE_BYTES = 20 * 1024 * 1024 - 1


@dataclass(frozen=True)
class ChatImportSession:
    upload_id: str
    participant_id: str
    submission_point: str
    filename: str
    tool: str
    source_type: str
    content_type: str
    size: int
    chunk_size: int
    total_chunks: int
    uploaded_chunks: tuple[int, ...] = ()
    status: str = "uploading"
    submission_id: str | None = None


class ChatImportRepository(Protocol):
    async def create(
        self,
        participant_id: str,
        submission_point: str,
        filename: str,
        tool: str,
        source_type: str,
        content_type: str,
        size: int,
    ) -> ChatImportSession: ...

    async def get(self, upload_id: str) -> ChatImportSession | None: ...

    async def put_chunk(self, upload_id: str, number: int, data: bytes) -> None: ...

    async def write_to_path(self, upload_id: str, target: Path) -> None: ...

    async def has_sha256(
        self, participant_id: str, archive_sha256: str, upload_ids: list[str]
    ) -> bool: ...

    async def has_submission(self, submission_id: str) -> bool: ...

    async def finalize_file(
        self, upload_id: str, archive_sha256: str, metadata: dict[str, Any]
    ) -> str: ...

    async def mark_completed(self, upload_id: str, submission_id: str) -> None: ...

    async def discard(self, upload_id: str) -> None: ...


class MongoChatImportRepository:
    def __init__(self, database: Any, bucket_name: str = "chat_uploads") -> None:
        self._sessions = database["chat_import_sessions"]
        self._submissions = database["chat_submissions"]
        self._files = database[f"{bucket_name}.files"]
        self._chunks = database[f"{bucket_name}.chunks"]

    async def create(
        self,
        participant_id: str,
        submission_point: str,
        filename: str,
        tool: str,
        source_type: str,
        content_type: str,
        size: int,
    ) -> ChatImportSession:
        upload_id = ObjectId()
        total_chunks = (size + DEFAULT_CHUNK_SIZE_BYTES - 1) // DEFAULT_CHUNK_SIZE_BYTES
        await asyncio.to_thread(
            self._sessions.insert_one,
            {
                "_id": upload_id,
                "participant_id": participant_id,
                "submission_point": submission_point,
                "filename": filename,
                "tool": tool,
                "source_type": source_type,
                "content_type": content_type,
                "size": size,
                "chunk_size": DEFAULT_CHUNK_SIZE_BYTES,
                "total_chunks": total_chunks,
                "status": "uploading",
                "created_at": datetime.now(UTC),
            },
        )
        return ChatImportSession(
            str(upload_id),
            participant_id,
            submission_point,
            filename,
            tool,
            source_type,
            content_type,
            size,
            DEFAULT_CHUNK_SIZE_BYTES,
            total_chunks,
        )

    async def get(self, upload_id: str) -> ChatImportSession | None:
        if not ObjectId.is_valid(upload_id):
            return None
        file_id = ObjectId(upload_id)
        document = await asyncio.to_thread(self._sessions.find_one, {"_id": file_id})
        if document is None:
            return None
        numbers = await asyncio.to_thread(
            lambda: sorted(
                item["n"] for item in self._chunks.find({"files_id": file_id}, {"n": 1})
            )
        )
        return ChatImportSession(
            upload_id,
            document["participant_id"],
            document["submission_point"],
            document["filename"],
            document["tool"],
            document["source_type"],
            document["content_type"],
            document["size"],
            document["chunk_size"],
            document["total_chunks"],
            tuple(numbers),
            document.get("status", "uploading"),
            document.get("submission_id"),
        )

    async def put_chunk(self, upload_id: str, number: int, data: bytes) -> None:
        session = await self.get(upload_id)
        if session is None or session.status != "uploading":
            raise ValueError("업로드 세션을 찾을 수 없습니다.")
        if number < 0 or number >= session.total_chunks:
            raise ValueError("chunk 번호가 올바르지 않습니다.")
        expected = min(session.chunk_size, session.size - number * session.chunk_size)
        if len(data) != expected:
            raise ValueError("chunk 크기가 올바르지 않습니다.")
        file_id = ObjectId(upload_id)
        existing = await asyncio.to_thread(
            self._chunks.find_one, {"files_id": file_id, "n": number}, {"data": 1}
        )
        if existing is not None:
            if bytes(existing["data"]) != data:
                raise ValueError("이미 저장된 chunk와 내용이 다릅니다.")
            return
        try:
            await asyncio.to_thread(
                self._chunks.insert_one,
                {"files_id": file_id, "n": number, "data": Binary(data)},
            )
        except DuplicateKeyError:
            existing = await asyncio.to_thread(
                self._chunks.find_one,
                {"files_id": file_id, "n": number},
                {"data": 1},
            )
            if existing is None or bytes(existing["data"]) != data:
                raise ValueError("이미 저장된 chunk와 내용이 다릅니다.")

    async def write_to_path(self, upload_id: str, target: Path) -> None:
        session = await self.get(upload_id)
        if session is None or session.uploaded_chunks != tuple(
            range(session.total_chunks)
        ):
            raise ValueError("아직 업로드되지 않은 chunk가 있습니다.")

        def write() -> None:
            with target.open("wb") as output:
                for chunk in self._chunks.find({"files_id": ObjectId(upload_id)}).sort(
                    "n", 1
                ):
                    output.write(chunk["data"])

        await asyncio.to_thread(write)

    async def has_sha256(
        self, participant_id: str, archive_sha256: str, upload_ids: list[str]
    ) -> bool:
        document = await asyncio.to_thread(
            self._files.find_one,
            {
                "_id": {"$nin": [ObjectId(upload_id) for upload_id in upload_ids]},
                "metadata.participant_id": participant_id,
                "metadata.import_sha256": archive_sha256,
            },
            {"_id": 1},
        )
        return document is not None

    async def has_submission(self, submission_id: str) -> bool:
        document = await asyncio.to_thread(
            self._submissions.find_one,
            {"client_submission_id": submission_id},
            {"_id": 1},
        )
        return document is not None

    async def finalize_file(
        self, upload_id: str, archive_sha256: str, metadata: dict[str, Any]
    ) -> str:
        session = await self.get(upload_id)
        if session is None or session.uploaded_chunks != tuple(
            range(session.total_chunks)
        ):
            raise ValueError("아직 업로드되지 않은 chunk가 있습니다.")
        file_id = ObjectId(upload_id)
        existing = await asyncio.to_thread(self._files.find_one, {"_id": file_id})
        if existing is not None:
            existing_sha256 = existing.get("metadata", {}).get("import_sha256")
            metadata_matches = existing_sha256 == archive_sha256
            length_matches = existing.get("length") == session.size
            chunk_size_matches = existing.get("chunkSize") == session.chunk_size
            if not (metadata_matches and length_matches and chunk_size_matches):
                raise ValueError("기존 완료 파일이 현재 업로드와 다릅니다.")
            return upload_id
        await asyncio.to_thread(
            self._files.insert_one,
            {
                "_id": file_id,
                "length": session.size,
                "chunkSize": session.chunk_size,
                "uploadDate": datetime.now(UTC),
                "filename": session.filename,
                "metadata": {**metadata, "import_sha256": archive_sha256},
            },
        )
        return upload_id

    async def mark_completed(self, upload_id: str, submission_id: str) -> None:
        await asyncio.to_thread(
            self._sessions.update_one,
            {"_id": ObjectId(upload_id)},
            {"$set": {"status": "completed", "submission_id": submission_id}},
        )

    async def discard(self, upload_id: str) -> None:
        if not ObjectId.is_valid(upload_id):
            return
        file_id = ObjectId(upload_id)
        await asyncio.to_thread(self._files.delete_one, {"_id": file_id})
        await asyncio.to_thread(self._chunks.delete_many, {"files_id": file_id})
        await asyncio.to_thread(self._sessions.delete_one, {"_id": file_id})


def inspect_gemini_zip(
    source: Path, max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
) -> dict[str, Any]:
    if not source.is_file():
        raise ValueError(f"파일을 찾을 수 없습니다: {source}")
    if source.suffix.lower() != ".zip" or not zipfile.is_zipfile(source):
        raise ValueError("유효한 ZIP 파일이 아닙니다.")
    size = source.stat().st_size
    if size > max_file_bytes:
        raise ValueError(
            f"관리자 주입 한도 {max_file_bytes // (1024 * 1024)}MB를 초과했습니다."
        )

    with zipfile.ZipFile(source) as archive:
        member_count = len(archive.infolist())
        expanded_bytes = sum(member.file_size for member in archive.infolist())
        if expanded_bytes > 4 * DEFAULT_MAX_FILE_BYTES:
            raise ValueError("ZIP 압축 해제 크기가 허용 범위를 초과했습니다.")
        if size and expanded_bytes / size > 100:
            raise ValueError("ZIP 압축률이 비정상적으로 높습니다.")
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
                if PurePosixPath(member.filename).name.lower()
                in {"myactivity.html", "내활동.html"}
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
        "memberCount": member_count,
        "geminiActivityFile": preferred.filename,
        "geminiActivityBytes": preferred.file_size,
    }


def inspect_chat_import(
    source: Path, tool: str, source_type: str, content_type: str
) -> str:
    if source_type == "image":
        valid_content_type = content_type in IMAGE_CONTENT_TYPES
        valid_size = source.stat().st_size <= MAX_IMAGE_BYTES
        if not (valid_content_type and valid_size):
            raise ValueError(
                "이미지는 JPG, PNG, WEBP, HEIC 형식이며 20MB 미만이어야 합니다."
            )
        signatures = {
            "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
            "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/webp": lambda data: (
                data.startswith(b"RIFF") and data[8:12] == b"WEBP"
            ),
            "image/heic": lambda data: data[4:8] == b"ftyp",
            "image/heif": lambda data: data[4:8] == b"ftyp",
        }
        with source.open("rb") as input_file:
            header = input_file.read(16)
        if not signatures[content_type](header):
            raise ValueError("이미지 파일 내용이 선택한 형식과 다릅니다.")
    else:
        if source.suffix.lower() != ".zip" or not zipfile.is_zipfile(source):
            raise ValueError("유효한 ZIP 파일이 아닙니다.")
        if tool == "gemini":
            inspect_gemini_zip(source)
        else:
            with zipfile.ZipFile(source) as archive:
                names = [member.filename.lower() for member in archive.infolist()]
                expanded_bytes = sum(member.file_size for member in archive.infolist())
                if expanded_bytes > 4 * DEFAULT_MAX_FILE_BYTES:
                    raise ValueError("ZIP 압축 해제 크기가 허용 범위를 초과했습니다.")
                archive_size = source.stat().st_size
                compression_ratio = expanded_bytes / archive_size if archive_size else 0
                if compression_ratio > 100:
                    raise ValueError("ZIP 압축률이 비정상적으로 높습니다.")
                if tool == "chatgpt" and not any(
                    PurePosixPath(name).name == "conversations.json" for name in names
                ):
                    raise ValueError(
                        "ZIP에서 ChatGPT conversations.json을 찾지 못했습니다."
                    )
                if tool == "claude":
                    find_claude_export_member(archive)
                if tool == "grok":
                    find_grok_export_member(archive)

    digest = hashlib.sha256()
    with source.open("rb") as input_file:
        while chunk := input_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
