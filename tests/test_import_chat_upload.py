import asyncio
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.db.json_storage import decode_json_storage, encode_json_storage
from app.services.chat_imports import inspect_chat_import
from app.services.transcript_adapters import normalize_export
from bson import ObjectId
from scripts.import_chat_upload import (
    import_submission,
    inspect_gemini_zip,
    normalize_phone,
    resumable_file_id,
)


def test_normalizes_participant_phone() -> None:
    assert normalize_phone("010-1234-5678") == "01012345678"
    with pytest.raises(ValueError, match="11자리"):
        normalize_phone("02-123-4567")


def test_large_transcript_storage_is_lossless_and_keeps_summary(monkeypatch) -> None:
    monkeypatch.setattr("app.db.json_storage.INLINE_LIMIT", 2048)
    monkeypatch.setattr("app.db.json_storage.COMPRESSED_LIMIT", 2048)
    payload = {"summary": {"totalTurns": 1}, "sessions": [{"turns": [{"content": "synthetic " * 2000}]}]}
    stored = encode_json_storage(payload)
    assert stored["storageEncoding"] == "gzip-json-v1"
    assert stored["summary"] == payload["summary"]
    assert decode_json_storage(stored) == payload
    assert "storageEncoding" not in payload
    corrupted = {**stored, "jsonSha256": "incorrect"}
    with pytest.raises(ValueError, match="integrity"):
        decode_json_storage(corrupted)
    with pytest.raises(ValueError, match="Unsupported"):
        decode_json_storage({**stored, "storageEncoding": "unknown"})


def test_small_transcript_storage_is_backwards_compatible() -> None:
    payload = {"sessions": [], "summary": {"totalTurns": 0}}
    assert encode_json_storage(payload) == payload
    assert decode_json_storage(payload) == payload
    assert decode_json_storage(None) is None


def test_large_storage_repository_write_and_read_roundtrip(monkeypatch) -> None:
    from app.services.chats import MongoChatSubmissionRepository, _record_from_document
    from app.services.transcript_runs import MongoTranscriptParseRunRepository, _project_transcript, _run

    monkeypatch.setattr("app.db.json_storage.INLINE_LIMIT", 2048)
    normalized = {"participantId": "synthetic", "platform": "gemini", "summary": {"totalTurns": 1},
                  "sessions": [{"turns": [{"role": "user", "content": "synthetic " * 2000}]}]}
    projected = _project_transcript(normalized, "synthetic-v1", [])
    collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
    identifier = str(ObjectId())
    assert asyncio.run(MongoChatSubmissionRepository(collection).update_transcript(identifier, projected))
    stored = collection.update_one.call_args.args[1]["$set"]["transcript"]
    document = {"_id": ObjectId(identifier), "participant_id": "synthetic", "submission_point": "afterRound2",
                "source_type": "file", "raw_input": "synthetic.zip", "submitted_at": datetime.now(UTC),
                "transcript": stored}
    assert _record_from_document(document).transcript == projected
    asyncio.run(MongoTranscriptParseRunRepository(collection).complete(identifier, "synthetic", "synthetic-v1", normalized, []))
    stored_run = collection.update_one.call_args.args[1]["$set"]
    assert stored_run["normalized_json"]["storageEncoding"] == "gzip-json-v1"
    run = _run({"_id": ObjectId(identifier), "submission_id": identifier, "schema_version": "transcript-v1",
                "created_at": datetime.now(UTC), **stored_run})
    assert run.normalized_json == normalized


def test_resumable_file_id_is_stable_and_scoped() -> None:
    first = resumable_file_id("participant-1", "afterRound1", "archive-sha")

    assert first == resumable_file_id("participant-1", "afterRound1", "archive-sha")
    assert first != resumable_file_id("participant-1", "afterRound4", "archive-sha")
    assert first != resumable_file_id("participant-2", "afterRound1", "archive-sha")


def test_inspects_gemini_takeout_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "Takeout/My Activity/Gemini Apps/MyActivity.html", "<html>activity</html>"
        )

    result = inspect_gemini_zip(archive_path)

    assert result["memberCount"] == 1
    assert result["geminiActivityFile"].endswith("MyActivity.html")
    assert len(result["archiveSha256"]) == 64


def test_rejects_zip_without_gemini_activity(tmp_path: Path) -> None:
    archive_path = tmp_path / "other.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Takeout/index.html", "<html></html>")

    with pytest.raises(ValueError, match="Gemini 내 활동 HTML"):
        inspect_gemini_zip(archive_path)


@pytest.mark.parametrize("product", ["AI 모드", "AI Mode"])
def test_gemini_import_accepts_product_verified_ai_mode(tmp_path: Path, product: str) -> None:
    source = tmp_path / "takeout.zip"
    html = f"""<div class="outer-cell"><div class="header-cell">{product}</div>
    <div class="content-cell"><a href="https://www.google.com/search?q=synthetic">Synthetic question</a><br>
    2026. 11. 7. 오후 1:02:03 KST<p>Synthetic answer</p></div></div>"""
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("Takeout/My Activity/Search/내 활동.html", html)
    inspection = inspect_gemini_zip(source)
    assert inspection["geminiActivityFile"].endswith("내 활동.html")
    assert inspect_chat_import(source, "gemini", "file", "application/zip") == inspection["archiveSha256"]
    _, _, normalized, _ = normalize_export("gemini", source.name, source.read_bytes(), "synthetic-participant")
    assert normalized["platform"] == "google_ai_mode"
    assert normalized["summary"]["totalTurns"] == 2


@pytest.mark.parametrize("product", ["Search", "Gmail", "YouTube"])
def test_gemini_import_does_not_accept_other_google_activity(tmp_path: Path, product: str) -> None:
    source = tmp_path / "takeout.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("MyActivity.html", f'<div class="outer-cell"><div class="header-cell">{product}</div>'
                        '<div class="content-cell">Synthetic activity</div></div>')
    with pytest.raises(ValueError):
        inspect_gemini_zip(source)


@pytest.mark.parametrize(
    "members",
    [
        ["nested/conversations.json"],
        ["nested/conversations-001.json", "nested/conversations-000.json"],
    ],
)
def test_chatgpt_import_accepts_supported_layouts(
    tmp_path: Path, members: list[str]
) -> None:
    source = tmp_path / "export.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for name in members:
            archive.writestr(name, "[]")

    digest = inspect_chat_import(source, "chatgpt", "file", "application/zip")

    assert len(digest) == 64


@pytest.mark.parametrize(
    "members",
    [
        ["user.json"],
        ["conversations-000.json", "conversations-002.json"],
        ["conversations-001.json"],
        ["conversations-0.json", "conversations-000.json"],
        ["conversations.json", "conversations-000.json"],
        ["first/conversations.json", "second/conversations.json"],
        ["first/conversations-000.json", "second/conversations-001.json"],
    ],
)
def test_chatgpt_import_and_parser_reject_ambiguous_or_incomplete_layouts(
    tmp_path: Path, members: list[str]
) -> None:
    source = tmp_path / "export.zip"
    with zipfile.ZipFile(source, "w") as archive:
        for name in members:
            archive.writestr(name, "[]")

    with pytest.raises(ValueError):
        inspect_chat_import(source, "chatgpt", "file", "application/zip")
    with pytest.raises(ValueError):
        normalize_export("chatgpt", source.name, source.read_bytes(), "participant-1")


def test_import_submission_associates_participant_and_file(tmp_path: Path) -> None:
    source = tmp_path / "takeout.zip"
    source.write_bytes(b"PK-test")
    database = SimpleNamespace(chat_submissions=MagicMock())
    inserted_id = ObjectId()
    database.chat_submissions.insert_one.return_value = SimpleNamespace(
        inserted_id=inserted_id
    )
    database.chat_submissions.find_one.return_value = {"_id": inserted_id}
    bucket = MagicMock()
    file_id = ObjectId()
    bucket.upload_from_stream.return_value = file_id

    result = import_submission(
        database,
        bucket,
        source,
        "participant-1",
        "afterRound1",
        "gemini",
        "id-1",
        "abc",
    )

    assert result == (str(inserted_id), str(file_id))
    document = database.chat_submissions.insert_one.call_args.args[0]
    assert document["participant_id"] == "participant-1"
    assert document["source_type"] == "file"
    assert document["tool"] == "gemini"
    assert document["attachments"][0]["fileId"] == str(file_id)
    assert document["import_sha256"] == "abc"
    metadata = bucket.upload_from_stream.call_args.kwargs["metadata"]
    assert metadata["source_type"] == "file"
    assert metadata["tool"] == "gemini"


def test_import_submission_rolls_back_gridfs_when_insert_fails(tmp_path: Path) -> None:
    source = tmp_path / "takeout.zip"
    source.write_bytes(b"PK-test")
    database = SimpleNamespace(chat_submissions=MagicMock())
    database.chat_submissions.insert_one.side_effect = RuntimeError("insert failed")
    bucket = MagicMock()
    file_id = ObjectId()
    bucket.upload_from_stream.return_value = file_id

    with pytest.raises(RuntimeError, match="insert failed"):
        import_submission(
            database,
            bucket,
            source,
            "participant-1",
            "afterRound1",
            "gemini",
            "id-1",
            "abc",
        )

    bucket.delete.assert_called_once_with(file_id)
