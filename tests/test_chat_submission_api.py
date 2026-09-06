import asyncio
from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.chat import ChatSubmissionRecord
from app.schemas.jobs import Job, JobCreate
from app.services.auth import (
    ParticipantAccount,
    ParticipantAccountRepository,
    ResearcherAccount,
    ResearcherAccountRepository,
)
from app.services.chats import ChatSubmissionRepository, store_chat_submission
from app.services.jobs import JobRepository, QueueWorker
from fastapi.testclient import TestClient


class InMemoryParticipants(ParticipantAccountRepository):
    def __init__(self, chat_consent: bool) -> None:
        self.account = ParticipantAccount(
            "participant-1", "01012345678", hash_password("password-2026"), False, chat_consent
        )

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.account if phone == self.account.phone else None

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return self.account if participant_id == self.account.participant_id else None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(self, phone: str, password_hash: str, chat_consent: bool = False) -> bool:
        return False

    async def create_imported(self, phone: str, password_hash: str, name: str, school_level: str, grade: int) -> bool:
        return False


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

    async def list_submissions(self, submission_point: str | None = None) -> list[ChatSubmissionRecord]:
        return self.submissions


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


def _app(chat_consent: bool) -> tuple[object, InMemoryJobs, InMemoryChatSubmissions]:
    jobs = InMemoryJobs()
    submissions = InMemoryChatSubmissions()
    app = create_app(
        Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
        participant_account_repository=InMemoryParticipants(chat_consent),
        researcher_account_repository=InMemoryResearchers(),
        job_repository=jobs,
        chat_submission_repository=submissions,
    )
    return app, jobs, submissions


def test_consented_participant_submission_is_parsed_and_saved() -> None:
    app, jobs, submissions = _app(True)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026"},
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


def test_participant_without_chat_consent_cannot_submit() -> None:
    app, _, _ = _app(False)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026"},
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


def test_researcher_can_export_completed_chat_submission() -> None:
    app, jobs, submissions = _app(True)
    with TestClient(app) as client:
        participant_login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026"},
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
