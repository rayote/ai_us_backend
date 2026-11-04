import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from scripts.parse_chat_backlog import (
    account_from_metadata,
    execute,
    skip_reason,
    stale_runs_recoverable,
)


@pytest.mark.parametrize(
    "projected,runs,retry,source,images,expected",
    [
        ("placeholder", [], False, "file", False, None),
        ("parsed", [], True, "file", False, "already_completed"),
        ("placeholder", ["completed", "failed"], True, "file", False, "already_completed"),
        ("placeholder", ["queued"], True, "file", False, "active_run"),
        ("placeholder", ["processing"], True, "file", False, "active_run"),
        ("placeholder", ["failed"], False, "file", False, "previously_requested"),
        ("placeholder", ["warning"], True, "file", False, None),
        ("placeholder", [], False, "image", False, "image_opt_in_required"),
        ("placeholder", [], False, "image", True, None),
    ],
)
def test_backlog_selection(projected, runs, retry, source, images, expected):
    account = account_from_metadata({"_id": ObjectId(), "chat_consent": True})
    submission = {
        "submitted_at": datetime.now(UTC),
        "transcript": {"status": projected},
        "source_type": source,
    }
    assert skip_reason(
        submission, account, [{"status": status} for status in runs],
        retry_incomplete=retry, include_images=images,
    ) == expected


def test_backlog_preserves_operational_consent_cutoff():
    cutoff = datetime.now(UTC)
    account = account_from_metadata({
        "_id": ObjectId(), "chat_consent": True, "chat_restricted_before": cutoff,
    })
    submission = {"submitted_at": cutoff - timedelta(seconds=1)}
    assert skip_reason(
        submission, account, [], retry_incomplete=True, include_images=True,
    ) == "consent_restricted"
    assert skip_reason(
        submission, None, [], retry_incomplete=True, include_images=True,
    ) == "consent_restricted"


def test_stale_recovery_requires_no_active_job_and_all_runs_older_than_one_hour():
    now = datetime.now(UTC)
    old = {"status": "queued", "created_at": now - timedelta(hours=2)}
    recent = {"status": "processing", "created_at": now - timedelta(minutes=5)}
    assert stale_runs_recoverable([old], False, now)
    assert not stale_runs_recoverable([old], True, now)
    assert not stale_runs_recoverable([old, recent], False, now)
    assert not stale_runs_recoverable([], False, now)


def test_dry_run_does_not_create_runs_or_modify_database(monkeypatch):
    import scripts.parse_chat_backlog as script

    run_repository = MagicMock()
    monkeypatch.setattr(script, "MongoTranscriptParseRunRepository", lambda _: run_repository)
    monkeypatch.setattr(script, "AsyncGridFSBucket", lambda *args: MagicMock())
    participant_id = ObjectId()
    metadata = {
        "_id": ObjectId(), "participant_id": str(participant_id),
        "submitted_at": datetime.now(UTC), "source_type": "file", "tool": "chatgpt",
    }
    database = SimpleNamespace(
        participants=MagicMock(), chat_submissions=MagicMock(),
        transcript_parse_runs=MagicMock(), submission_jobs=MagicMock(),
    )
    database.participants.find_one.return_value = {"_id": participant_id, "chat_consent": True}
    database.chat_submissions.find.return_value.sort.return_value = [metadata]
    database.transcript_parse_runs.find.return_value = []
    database.submission_jobs.find_one.return_value = None
    args = SimpleNamespace(
        include_images=False, submission_id=None, recover_stale=False,
        retry_incomplete=False, max_items=1, apply=False,
    )
    report = io.StringIO()
    counts = asyncio.run(execute(args, database, report))

    assert counts == {"eligible": 1}
    assert json.loads(report.getvalue())["status"] == "eligible"
    run_repository.create.assert_not_called()
    database.chat_submissions.update_one.assert_not_called()
    database.transcript_parse_runs.update_one.assert_not_called()