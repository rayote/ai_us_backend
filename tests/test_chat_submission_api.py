import asyncio
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.chat import ChatSubmissionRecord, ParsedTranscript
from app.schemas.jobs import Job, JobCreate
from app.services.auth import (
    ParticipantAccount,
    ParticipantAccountRepository,
    ResearcherAccount,
    ResearcherAccountRepository,
)
from app.services.chats import (
    ChatSubmissionRepository,
    ChatUploadRepository,
    build_chat_archive,
    store_chat_submission,
)
from app.services.jobs import JobRepository, QueueWorker
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


def _app(chat_consent: bool) -> tuple[object, InMemoryJobs, InMemoryChatSubmissions, InMemoryChatUploads]:
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

    assert zip_response.json() == {"submissionId": "zip-1", "status": "completed"}
    assert image_response.json() == {"submissionId": "image-1", "status": "completed"}
    assert len(uploads.files) == 3
    assert submissions.submissions[0].attachments[0].filename == "chat-export.zip"
    assert [attachment.filename for attachment in submissions.submissions[1].attachments] == [
        "chat-1.png",
        "chat-2.jpg",
    ]


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
