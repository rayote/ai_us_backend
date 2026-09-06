import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.jobs import Job, JobCreate
from app.schemas.survey import SurveyDefinition, SurveyQuestion, SurveyResponseRecord
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from app.services.jobs import JobRepository, QueueWorker
from app.services.submissions import store_survey_response
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository


class InMemoryParticipantAccounts(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.account = ParticipantAccount("participant-1", "01012345678", hash_password("password-2026"), False)

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.account if phone == self.account.phone else None

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return self.account if participant_id == self.account.participant_id else None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(self, phone: str, password_hash: str) -> bool:
        return False


class InMemoryDefinitions(SurveyDefinitionRepository):
    def __init__(self) -> None:
        self.definition = SurveyDefinition(
            surveyRound=1,
            surveyVersion="2026-round-1-v1",
            questions=[SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1)],
            createdAt=datetime.now(UTC),
        )

    async def create(self, definition: object) -> SurveyDefinition:
        raise NotImplementedError

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None:
        if (survey_round, survey_version) == (self.definition.survey_round, self.definition.survey_version):
            return self.definition
        return None


class InMemoryResponses(SurveyResponseRepository):
    def __init__(self) -> None:
        self.responses: list[SurveyResponseRecord] = []

    async def create_response(self, response: SurveyResponseRecord) -> None:
        self.responses.append(response)

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]:
        return self.responses


class InMemoryJobs(JobRepository):
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def enqueue(self, job: JobCreate) -> Job:
        for existing in self.jobs:
            if (existing.job_type, existing.idempotency_key) == (job.job_type, job.idempotency_key):
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


def test_submission_is_queued_then_completed_by_worker() -> None:
    definitions = InMemoryDefinitions()
    responses = InMemoryResponses()
    jobs = InMemoryJobs()
    app = create_app(
        Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=definitions,
        survey_response_repository=responses,
        job_repository=jobs,
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026"},
        )
        headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}
        submit = client.post(
            "/api/v1/survey-responses",
            headers=headers,
            json={
                "surveyRound": 1,
                "surveyVersion": "2026-round-1-v1",
                "answers": {"q1": "응답"},
                "submissionId": "browser-submission-1",
            },
        )
        submission_id = submit.json()["submissionId"]
        queued_status = client.get(f"/api/v1/submission-jobs/{submission_id}", headers=headers)
        worker = QueueWorker(
            jobs,
            {"survey_response": lambda payload: store_survey_response(payload, definitions, responses)},
        )
        assert asyncio.run(worker.process_one()) is True
        completed_status = client.get(f"/api/v1/submission-jobs/{submission_id}", headers=headers)

    assert submit.status_code == 202
    assert queued_status.json()["status"] == "queued"
    assert completed_status.json()["status"] == "completed"
    assert responses.responses[0].answers == {"q1": "응답"}


def test_submission_rejects_unknown_question_key() -> None:
    app = create_app(
        Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=InMemoryDefinitions(),
        survey_response_repository=InMemoryResponses(),
        job_repository=InMemoryJobs(),
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={"phone": "01012345678", "password": "password-2026"},
        )
        response = client.post(
            "/api/v1/survey-responses",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            json={
                "surveyRound": 1,
                "surveyVersion": "2026-round-1-v1",
                "answers": {"unknown": "응답"},
                "submissionId": "browser-submission-2",
            },
        )

    assert response.status_code == 422