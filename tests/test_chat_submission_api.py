import asyncio
import re
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.chat import ChatSubmissionRecord, ParsedTranscript, TranscriptParseRun
from app.schemas.jobs import Job, JobCreate
from app.services.auth import (
    ParticipantAccount,
    ParticipantAccountRepository,
    ResearcherAccount,
    ResearcherAccountRepository,
)
from app.services.chat_downloads import chat_download_filename
from app.services.chats import (
    ChatSubmissionRepository,
    ChatUploadRepository,
    build_chat_archive,
    store_chat_submission,
)
from app.services.jobs import JobRepository, QueueWorker
from app.services.transcript_runs import TranscriptParseRunRepository
from fastapi.testclient import TestClient


class InMemoryParticipants(ParticipantAccountRepository):
    def __init__(self, chat_consent: bool) -> None:
        self.account = ParticipantAccount(
            "participant-1", "01012345678", hash_password("password-2026"), False, chat_consent, "초등"
        )

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.account if phone == self.account.phone else None

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return self.account if participant_id == self.account.participant_id else None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(
        self, phone: str, password_hash: str, chat_consent: bool = False, school_level: str | None = None
    ) -> bool:
        return False

    async def create_imported(self, phone: str, password_hash: str, name: str, school_level: str, grade: int) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return [self.account]


class InMemoryJobs(JobRepository):
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def enqueue(self, job: JobCreate) -> Job:
        existing = next(
            (
                item
                for item in self.jobs
                if item.job_type == job.job_type and item.idempotency_key == job.idempotency_key
            ),
            None,
        )
        if existing is not None:
            return existing
        queued = Job(
            id=str(len(self.jobs) + 1),
            job_type=job.job_type,
            idempotency_key=job.idempotency_key,
            payload=job.payload,
            status="queued",
            attempts=0,
            created_at=datetime.now(UTC),
        )
        self.jobs.append(queued)
        return queued

    async def get(self, job_id: str) -> Job | None:
        return next((job for job in self.jobs if job.id == job_id), None)

    async def recover_interrupted(self) -> int:
        return 0

    async def claim_next(self) -> Job | None:
        for index, job in enumerate(self.jobs):
            if job.status == "queued":
                claimed = job.model_copy(update={"status": "processing", "attempts": job.attempts + 1})
                self.jobs[index] = claimed
                return claimed
        return None

    async def complete(self, job_id: str) -> None:
        self._update(job_id, status="completed")

    async def retry(self, job_id: str, error: str) -> None:
        self._update(job_id, status="queued", error=error)

    async def fail(self, job_id: str, error: str) -> None:
        self._update(job_id, status="failed", error=error)

    def _update(self, job_id: str, **changes: object) -> None:
        for index, job in enumerate(self.jobs):
            if job.id == job_id:
                self.jobs[index] = job.model_copy(update=changes)
                return
        raise AssertionError(f"Unknown job: {job_id}")


class InMemoryTranscriptParseRuns(TranscriptParseRunRepository):
    def __init__(self) -> None:
        self.runs: list[TranscriptParseRun] = []

    async def create(self, submission_id: str, parser_name: str, parser_version: str) -> TranscriptParseRun:
        run = TranscriptParseRun(
            runId=str(len(self.runs) + 1),
            submissionId=submission_id,
            status="queued",
            parserName=parser_name,
            parserVersion=parser_version,
            schemaVersion="transcript-v1",
            createdAt=datetime.now(UTC),
        )
        self.runs.append(run)
        return run

    async def get(self, run_id: str) -> TranscriptParseRun | None:
        return next((run for run in self.runs if run.run_id == run_id), None)

    async def latest(self, submission_id: str) -> TranscriptParseRun | None:
        return next(
            (run for run in reversed(self.runs) if run.submission_id == submission_id and run.status == "completed"),
            None,
        )

    async def active(self, submission_id: str) -> TranscriptParseRun | None:
        return next(
            (
                run
                for run in reversed(self.runs)
                if run.submission_id == submission_id and run.status in {"queued", "processing"}
            ),
            None,
        )

    async def complete(
        self,
        run_id: str,
        parser_name: str,
        parser_version: str,
        normalized_json: dict[str, Any],
        warnings: list[str],
        status: str = "completed",
    ) -> None:
        self._update(
            run_id,
            status=status,
            parser_name=parser_name,
            parser_version=parser_version,
            normalized_json=normalized_json,
            warnings=warnings,
            completed_at=datetime.now(UTC),
        )

    async def fail(self, run_id: str, error: str, warnings: list[str]) -> None:
        self._update(
            run_id,
            status="failed",
            error=error,
            warnings=warnings,
            completed_at=datetime.now(UTC),
        )

    def _update(self, run_id: str, **changes: object) -> None:
        for index, run in enumerate(self.runs):
            if run.run_id == run_id:
                self.runs[index] = run.model_copy(update=changes)
                return
        raise AssertionError(f"Unknown parse run: {run_id}")


class InMemoryChatSubmissions(ChatSubmissionRepository):
    def __init__(self) -> None:
        self.submissions: list[ChatSubmissionRecord] = []

    async def create_submission(self, submission: ChatSubmissionRecord) -> None:
        self.submissions.append(submission)

    async def list_submissions(
        self, submission_point: str | None = None, status: str = "active"
    ) -> list[ChatSubmissionRecord]:
        return [
            submission
            for submission in self.submissions
            if (submission_point is None or submission.submission_point == submission_point)
            and submission.status == status
        ]

    async def list_for_participant(self, participant_id: str) -> list[ChatSubmissionRecord]:
        return [submission for submission in self.submissions if submission.participant_id == participant_id]

    async def request_deletion(self, submission_id: str, participant_id: str) -> bool:
        for index, submission in enumerate(self.submissions):
            if (
                submission.submission_id == submission_id
                and submission.participant_id == participant_id
                and submission.status == "active"
            ):
                self.submissions[index] = submission.model_copy(
                    update={"status": "deletion_requested", "deletion_requested_at": datetime.now(UTC)}
                )
                return True
        return False

    async def restore_deletion(self, submission_id: str, participant_id: str) -> bool:
        for index, submission in enumerate(self.submissions):
            if (
                submission.submission_id == submission_id
                and submission.participant_id == participant_id
                and submission.status == "deletion_requested"
            ):
                self.submissions[index] = submission.model_copy(
                    update={"status": "active", "deletion_requested_at": None}
                )
                return True
        return False

    async def get_for_deletion(self, submission_id: str) -> ChatSubmissionRecord | None:
        return next(
            (
                submission
                for submission in self.submissions
                if submission.submission_id == submission_id and submission.status == "deletion_requested"
            ),
            None,
        )

    async def remove(self, submission_id: str) -> bool:
        for index, submission in enumerate(self.submissions):
            if submission.submission_id == submission_id and submission.status == "deletion_requested":
                self.submissions.pop(index)
                return True
        return False

    async def update_transcript(self, submission_id: str, transcript: ParsedTranscript) -> bool:
        for index, submission in enumerate(self.submissions):
            if submission.submission_id == submission_id:
                self.submissions[index] = submission.model_copy(update={"transcript": transcript})
                return True
        return False


class InMemoryChatUploads(ChatUploadRepository):
    def __init__(self) -> None:
        self.files: dict[str, tuple[str, bytes, dict[str, object]]] = {}

    async def upload(self, filename: str, data: bytes, metadata: dict[str, object]) -> str:
        file_id = str(len(self.files) + 1)
        self.files[file_id] = (filename, data, metadata)
        return file_id

    async def delete(self, file_id: str) -> None:
        self.files.pop(file_id, None)

    async def download_to_path(self, file_id: str, target: Path) -> None:
        target.write_bytes(self.files[file_id][1])


class InMemoryResearchers(ResearcherAccountRepository):
    def __init__(self) -> None:
        self.account = ResearcherAccount(
            "researcher-1", "researcher", hash_password("researcher-password"), "researcher"
        )

    async def find_by_username(self, username: str) -> ResearcherAccount | None:
        return self.account if username == self.account.username else None

    async def ensure_bootstrap(self, username: str, password_hash: str) -> None:
        return None

    async def create(self, username: str, password_hash: str, role: str) -> str | None:
        return None


def _app(
    chat_consent: bool, transcript_runs: TranscriptParseRunRepository | None = None
) -> tuple[object, InMemoryJobs, InMemoryChatSubmissions, InMemoryChatUploads]:
    jobs = InMemoryJobs()
    submissions = InMemoryChatSubmissions()
    uploads = InMemoryChatUploads()
    app = create_app(
        Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
        participant_account_repository=InMemoryParticipants(chat_consent),
        researcher_account_repository=InMemoryResearchers(),
        job_repository=jobs,
        chat_submission_repository=submissions,
        chat_upload_repository=uploads,
        transcript_parse_run_repository=transcript_runs,
    )
    return app, jobs, submissions, uploads


def test_consented_participant_submission_is_parsed_and_saved() -> None:
    app, jobs, submissions, _ = _app(True)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
        response = client.post(
            "/api/v1/chat-submissions",
            headers=headers,
            json={
                "submissionPoint": "afterRound1",
                "sourceType": "text",
                "rawInput": "사용자: 안녕하세요\nAI: 반가워요",
                "submissionId": "chat-1",
            },
        )
        worker = QueueWorker(jobs, {"chat_submission": lambda payload: store_chat_submission(payload, submissions)})
        assert asyncio.run(worker.process_one()) is True
        status_response = client.get(f"/api/v1/submission-jobs/{response.json()['submissionId']}", headers=headers)

    assert response.status_code == 202
    assert status_response.json()["status"] == "completed"
    assert submissions.submissions[0].raw_input == "사용자: 안녕하세요\nAI: 반가워요"
    assert submissions.submissions[0].transcript.status == "parsed"


def test_original_archive_includes_deletion_requested_attachments_only_when_requested() -> None:
    uploads = InMemoryChatUploads()
    file_id = asyncio.run(uploads.upload("original.txt", b"original", {}))
    submission = ChatSubmissionRecord(
        submissionId="chat-deletion-requested",
        participantId="participant-1",
        submissionPoint="afterRound1",
        sourceType="file",
        rawInput="",
        transcript=ParsedTranscript(status="placeholder", parserVersion="v1", messages=[], plainText="", warnings=[]),
        submittedAt=datetime.now(UTC),
        attachments=[{"fileId": file_id, "filename": "original.txt", "contentType": "text/plain", "size": 8}],
        status="deletion_requested",
    )
    with tempfile.TemporaryDirectory() as directory:
        bulk_path = Path(directory) / "bulk.zip"
        original_path = Path(directory) / "original.zip"
        assert asyncio.run(build_chat_archive([submission], uploads, bulk_path)) == 0
        assert (
            asyncio.run(build_chat_archive([submission], uploads, original_path, include_deletion_requested=True)) == 1
        )
        with zipfile.ZipFile(original_path) as archive:
            assert archive.read("chat-deletion-requested/original.txt") == b"original"


def test_bulk_chat_download_filename_uses_kst() -> None:
    assert chat_download_filename(datetime(2026, 9, 22, 15, 4, 5, tzinfo=UTC)) == (
        "chat-submissions_2026-09-23_00-04-05.zip"
    )


def test_participant_without_chat_consent_cannot_submit() -> None:
    app, _, _, _ = _app(False)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        response = client.post(
            "/api/v1/chat-submissions",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            json={
                "submissionPoint": "afterRound1",
                "sourceType": "text",
                "rawInput": "사용자: 안녕하세요",
                "submissionId": "chat-2",
            },
        )

    assert response.status_code == 403


def test_legacy_snake_case_transcript_fields_are_accepted() -> None:
    transcript = ParsedTranscript.model_validate(
        {
            "status": "placeholder",
            "parser_version": "attachment-v1",
            "messages": [],
            "plain_text": "",
            "warnings": ["원본 파일"],
        }
    )

    assert transcript.parser_version == "attachment-v1"
    assert transcript.plain_text == ""


def test_researcher_can_export_completed_chat_submission() -> None:
    app, jobs, submissions, _ = _app(True)
    with TestClient(app) as client:
        participant_login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        submit = client.post(
            "/api/v1/chat-submissions",
            headers={"Authorization": f"Bearer {participant_login.json()['accessToken']}"},
            json={
                "submissionPoint": "afterRound4",
                "sourceType": "text",
                "rawInput": "사용자: 마지막 대화",
                "submissionId": "chat-3",
            },
        )
        worker = QueueWorker(jobs, {"chat_submission": lambda payload: store_chat_submission(payload, submissions)})
        asyncio.run(worker.process_one())
        researcher_login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        export = client.get(
            "/api/v1/researcher/exports/chat-submissions",
            headers={"Authorization": f"Bearer {researcher_login.json()['accessToken']}"},
            params={"submission_point": "afterRound4"},
        )

    assert submit.status_code == 202
    assert export.status_code == 200
    assert "마지막 대화" in export.text


def test_researcher_chat_export_filters_selected_submissions_and_uses_kst_filename() -> None:
    app, _, submissions, _ = _app(True)
    for submission_id, raw_input in (("selected-chat", "선택한 대화"), ("other-chat", "제외할 대화")):
        asyncio.run(
            submissions.create_submission(
                ChatSubmissionRecord(
                    submissionId=submission_id,
                    participantId="participant-1",
                    submissionPoint="afterRound1",
                    sourceType="text",
                    rawInput=raw_input,
                    transcript=ParsedTranscript(
                        status="parsed", parserVersion="v1", messages=[], plainText=raw_input, warnings=[]
                    ),
                    submittedAt=datetime.now(UTC),
                )
            )
        )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        response = client.get(
            "/api/v1/researcher/exports/chat-submissions",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            params={"submission_id": "selected-chat"},
        )

    assert response.status_code == 200
    assert "선택한 대화" in response.text
    assert "제외할 대화" not in response.text
    assert re.fullmatch(
        r'attachment; filename="chat-submissions-all_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.csv"',
        response.headers["content-disposition"],
    )


def test_consented_participant_can_upload_zip_and_multiple_images() -> None:
    app, _, submissions, uploads = _app(True)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
        zip_response = client.post(
            "/api/v1/chat-submissions/uploads",
            headers=headers,
            data={"tool": "chatgpt", "submissionPoint": "afterRound1", "sourceType": "file", "submissionId": "zip-1"},
            files={"files": ("chat-export.zip", b"PK\x03\x04example", "application/zip")},
        )
        image_response = client.post(
            "/api/v1/chat-submissions/uploads",
            headers=headers,
            data={"tool": "zeta", "submissionPoint": "afterRound4", "sourceType": "image", "submissionId": "image-1"},
            files=[
                ("files", ("chat-1.png", b"png-data", "image/png")),
                ("files", ("chat-2.jpg", b"jpg-data", "image/jpeg")),
            ],
        )
        researcher_login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        preview = client.get(
            "/api/v1/researcher/chat-submission-previews",
            headers={"Authorization": f"Bearer {researcher_login.json()['accessToken']}"},
        )

    assert zip_response.json() == {"submissionId": "zip-1", "status": "completed"}
    assert image_response.json() == {"submissionId": "image-1", "status": "completed"}
    assert len(uploads.files) == 3
    assert submissions.submissions[0].attachments[0].filename == "chat-export.zip"
    assert [attachment.filename for attachment in submissions.submissions[1].attachments] == [
        "chat-1.png",
        "chat-2.jpg",
    ]
    assert {item["parseStatus"] for item in preview.json()} == {"placeholder"}


def test_bulk_chat_download_job_keeps_selected_submission_ids_and_filters() -> None:
    app, jobs, _, _ = _app(True)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        response = client.post(
            "/api/v1/researcher/chat-submissions/download-jobs",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            json={
                "submissionIds": ["chat-a", "chat-b"],
                "submissionPoint": "afterRound1",
                "schoolLevel": "초등",
            },
        )

    assert response.status_code == 202
    assert jobs.jobs[0].job_type == "chat_download"
    assert jobs.jobs[0].payload["submissionIds"] == ["chat-a", "chat-b"]
    assert jobs.jobs[0].payload["submissionPoint"] == "afterRound1"
    assert jobs.jobs[0].payload["schoolLevel"] == "초등"


def test_transcript_downloads_use_kst_timestamped_filenames() -> None:
    runs = InMemoryTranscriptParseRuns()
    run = asyncio.run(runs.create("grok-download", "grok-json", "grok-json-v1"))
    asyncio.run(
        runs.complete(
            run.run_id,
            "grok-json",
            "grok-json-v1",
            {"participantId": "participant-1", "platform": "grok", "sessions": []},
            [],
        )
    )
    app, _, _, _ = _app(True, runs)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
        csv_response = client.get(
            "/api/v1/researcher/chat-submissions/grok-download/latest-parse/download?format=csv",
            headers=headers,
        )
        json_response = client.get(
            "/api/v1/researcher/chat-submissions/grok-download/latest-parse/download?format=json",
            headers=headers,
        )

    assert csv_response.status_code == 200
    assert json_response.status_code == 200
    pattern = r'attachment; filename="transcript-grok-download_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.(csv|json)"'
    assert re.fullmatch(pattern, csv_response.headers["content-disposition"])
    assert re.fullmatch(pattern, json_response.headers["content-disposition"])


def test_chat_preview_restores_persisted_parsed_transcript_status() -> None:
    app, _, submissions, _ = _app(True)
    asyncio.run(
        submissions.create_submission(
            ChatSubmissionRecord(
                submissionId="persisted-parse",
                participantId="participant-1",
                submissionPoint="afterRound1",
                sourceType="file",
                tool="grok",
                rawInput="grok.zip",
                transcript=ParsedTranscript(
                    status="parsed",
                    parserVersion="grok-json-v1",
                    messages=[{"speaker": "user", "text": "저장된 대화"}],
                    plainText="user: 저장된 대화",
                    warnings=[],
                ),
                submittedAt=datetime.now(UTC),
            )
        )
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        response = client.get(
            "/api/v1/researcher/chat-submission-previews",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
        )

    assert response.status_code == 200
    assert response.json()[0]["submissionId"] == "persisted-parse"
    assert response.json()[0]["parseStatus"] == "parsed"


def test_reparse_request_hides_previous_parse_until_completion() -> None:
    runs = InMemoryTranscriptParseRuns()
    app, _, submissions, _ = _app(True, runs)
    asyncio.run(
        submissions.create_submission(
            ChatSubmissionRecord(
                submissionId="reparse-pending",
                participantId="participant-1",
                submissionPoint="afterRound1",
                sourceType="file",
                tool="gemini",
                rawInput="takeout.zip",
                transcript=ParsedTranscript(
                    status="parsed",
                    parserVersion="gemini-takeout-html-v1",
                    messages=[{"speaker": "user", "text": "이전 결과"}],
                    plainText="user: 이전 결과",
                    warnings=[],
                ),
                submittedAt=datetime.now(UTC),
            )
        )
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
        requested = client.post("/api/v1/researcher/chat-submissions/reparse-pending/parse", headers=headers)
        preview = client.get("/api/v1/researcher/chat-submission-previews", headers=headers)

    assert requested.status_code == 202
    assert preview.status_code == 200
    assert preview.json()[0]["parseStatus"] == "placeholder"


def test_repeated_transcript_parse_request_reuses_active_run_and_job() -> None:
    runs = InMemoryTranscriptParseRuns()
    app, jobs, _, _ = _app(True, runs)
    with TestClient(app) as client:
        participant_login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        client.post(
            "/api/v1/chat-submissions/uploads",
            headers={"Authorization": f"Bearer {participant_login.json()['accessToken']}"},
            data={"tool": "grok", "submissionPoint": "afterRound1", "sourceType": "file", "submissionId": "grok-1"},
            files={"files": ("grok.zip", b"PK\x03\x04example", "application/zip")},
        )
        researcher_login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        headers = {"Authorization": f"Bearer {researcher_login.json()['accessToken']}"}
        first = client.post("/api/v1/researcher/chat-submissions/grok-1/parse", headers=headers)
        repeated = client.post("/api/v1/researcher/chat-submissions/grok-1/parse", headers=headers)

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json() == first.json()
    assert len(runs.runs) == 1
    assert len(jobs.jobs) == 1


def test_transcript_parse_request_replaces_active_run_with_terminal_job() -> None:
    runs = InMemoryTranscriptParseRuns()
    app, jobs, _, _ = _app(True, runs)
    with TestClient(app) as client:
        participant_login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        client.post(
            "/api/v1/chat-submissions/uploads",
            headers={"Authorization": f"Bearer {participant_login.json()['accessToken']}"},
            data={"tool": "grok", "submissionPoint": "afterRound1", "sourceType": "file", "submissionId": "grok-2"},
            files={"files": ("grok.zip", b"PK\x03\x04example", "application/zip")},
        )
        researcher_login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        headers = {"Authorization": f"Bearer {researcher_login.json()['accessToken']}"}
        first = client.post("/api/v1/researcher/chat-submissions/grok-2/parse", headers=headers)
        jobs._update(first.json()["jobId"], status="failed", error="worker failed")
        retried = client.post("/api/v1/researcher/chat-submissions/grok-2/parse", headers=headers)

    assert retried.status_code == 202
    assert retried.json()["runId"] != first.json()["runId"]
    assert retried.json()["jobId"] != first.json()["jobId"]
    assert runs.runs[0].status == "failed"
    assert runs.runs[1].status == "queued"
    assert len(jobs.jobs) == 2


def test_chat_upload_rejects_invalid_file_type() -> None:
    app, _, _, uploads = _app(True)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        response = client.post(
            "/api/v1/chat-submissions/uploads",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            data={"tool": "chatgpt", "submissionPoint": "afterRound1", "sourceType": "file", "submissionId": "bad-1"},
            files={"files": ("not-a-zip.txt", b"text", "text/plain")},
        )

    assert response.status_code == 422
    assert "ZIP" in response.json()["detail"]
    assert uploads.files == {}


def test_participant_can_manage_history_and_researcher_deletes_requested_upload() -> None:
    app, _, submissions, uploads = _app(True)
    with TestClient(app) as client:
        participant_login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026", "audience": "elementary"},
        )
        participant_headers = {"Authorization": f"Bearer {participant_login.json()['accessToken']}"}
        client.post(
            "/api/v1/chat-submissions/uploads",
            headers=participant_headers,
            data={
                "tool": "chatgpt",
                "submissionPoint": "afterRound1",
                "sourceType": "file",
                "submissionId": "history-1",
            },
            files={"files": ("history.zip", b"PK\x03\x04example", "application/zip")},
        )
        history = client.get("/api/v1/chat-submissions/mine", headers=participant_headers)
        submission_id = history.json()[0]["submissionId"]
        request_deletion = client.post(
            f"/api/v1/chat-submissions/{submission_id}/deletion-request", headers=participant_headers
        )
        restore = client.post(f"/api/v1/chat-submissions/{submission_id}/restore", headers=participant_headers)
        client.post(f"/api/v1/chat-submissions/{submission_id}/deletion-request", headers=participant_headers)
        researcher_login = client.post(
            "/api/v1/auth/researcher/login",
            json={"username": "researcher", "password": "researcher-password"},
        )
        researcher_headers = {"Authorization": f"Bearer {researcher_login.json()['accessToken']}"}
        requested = client.get("/api/v1/researcher/chat-submissions/files", headers=researcher_headers)
        original = client.get(
            f"/api/v1/researcher/chat-submissions/files/{submission_id}/download", headers=researcher_headers
        )
        deleted = client.delete(
            f"/api/v1/researcher/chat-submissions/files/{submission_id}", headers=researcher_headers
        )
        final_history = client.get("/api/v1/chat-submissions/mine", headers=participant_headers)

    assert history.json()[0]["filenames"] == ["history.zip"]
    assert history.json()[0]["status"] == "active"
    assert request_deletion.json()["status"] == "deletion_requested"
    assert restore.json()["status"] == "active"
    assert original.status_code == 200
    assert original.headers["content-type"].startswith("application/zip")
    assert requested.json()[0]["submissionId"] == submission_id
    assert deleted.json() == {"status": "deleted"}
    assert final_history.json() == []
    assert submissions.submissions == []
    assert uploads.files == {}
