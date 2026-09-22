import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from scripts.import_chat_upload import import_submission, inspect_gemini_zip, normalize_phone


def test_normalizes_participant_phone() -> None:
    assert normalize_phone("010-1234-5678") == "01012345678"
    with pytest.raises(ValueError, match="11자리"):
        normalize_phone("02-123-4567")


def test_inspects_gemini_takeout_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / "takeout.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Takeout/My Activity/Gemini Apps/MyActivity.html", "<html>activity</html>")

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


def test_import_submission_associates_participant_and_file(tmp_path: Path) -> None:
    source = tmp_path / "takeout.zip"
    source.write_bytes(b"PK-test")
    database = SimpleNamespace(chat_submissions=MagicMock())
    inserted_id = ObjectId()
    database.chat_submissions.insert_one.return_value = SimpleNamespace(inserted_id=inserted_id)
    database.chat_submissions.find_one.return_value = {"_id": inserted_id}
    bucket = MagicMock()
    file_id = ObjectId()
    bucket.upload_from_stream.return_value = file_id

    result = import_submission(database, bucket, source, "participant-1", "afterRound1", "gemini", "id-1", "abc")

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
        import_submission(database, bucket, source, "participant-1", "afterRound1", "gemini", "id-1", "abc")

    bucket.delete.assert_called_once_with(file_id)
