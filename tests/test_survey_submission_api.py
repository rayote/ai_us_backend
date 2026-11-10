import asyncio
from datetime import UTC, datetime

import pytest
from app.core.security import create_access_token, hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.jobs import Job, JobCreate
from app.schemas.survey import SurveyDefinition, SurveyQuestion, SurveyResponseRecord
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from app.services.jobs import JobRepository, QueueWorker
from app.services.submissions import store_survey_response
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository
from fastapi.testclient import TestClient


@pytest.fixture
def open_participation_policy(monkeypatch):
    from app.api import applications, chats, survey

    original = applications.participation_settings

    async def policy(request):
        if getattr(request.app.state, "application_settings_repository", None) is None:
            return {"participation_paused": False}
        return await original(request)

    for module in (applications, chats, survey):
        monkeypatch.setattr(module, "participation_settings", policy)


class InMemoryParticipantAccounts(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.account = ParticipantAccount(
            "participant-1",
            "01012345678",
            hash_password("password-2026"),
            False,
            False,
            "초등",
            grade=4,
            guardian_phone="01098765432",
        )

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

    async def get(
        self, survey_round: int, survey_version: str
    ) -> SurveyDefinition | None:
        if (survey_round, survey_version) == (
            self.definition.survey_round,
            self.definition.survey_version,
        ):
            return self.definition
        return None


class InMemoryResponses(SurveyResponseRepository):
    def __init__(self) -> None:
        self.responses: list[SurveyResponseRecord] = []

    async def create_response(self, response: SurveyResponseRecord) -> None:
        self.responses.append(response)

    async def list_responses(
        self, survey_round: int, survey_version: str
    ) -> list[SurveyResponseRecord]:
        return self.responses

    async def list_versions_for_participant(
        self, participant_id: str, survey_round: int
    ) -> list[str]:
        return [
            response.survey_version
            for response in self.responses
            if response.participant_id == participant_id
            and response.survey_round == survey_round
        ]


class InMemoryJobs(JobRepository):
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    async def enqueue(self, job: JobCreate) -> Job:
        for existing in self.jobs:
            if (existing.job_type, existing.idempotency_key) == (
                job.job_type,
                job.idempotency_key,
            ):
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

    async def recover_interrupted(self, max_attempts: int = 3) -> int:
        return 0

    async def claim_next(self) -> Job | None:
        for index, job in enumerate(self.jobs):
            if job.status == "queued":
                claimed = job.model_copy(
                    update={"status": "processing", "attempts": job.attempts + 1}
                )
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


class InMemorySurveySessions:
    def __init__(self) -> None:
        self.heartbeats: list[tuple[str, object]] = []

    async def heartbeat(self, participant_id: str, heartbeat: object) -> None:
        self.heartbeats.append((participant_id, heartbeat))


def test_submission_is_queued_then_completed_by_worker(open_participation_policy) -> None:
    definitions = InMemoryDefinitions()
    responses = InMemoryResponses()
    jobs = InMemoryJobs()
    app = create_app(
        Settings(
            "test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60
        ),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=definitions,
        survey_response_repository=responses,
        job_repository=jobs,
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={
                "phone": "01012345678",
                "password": "password-2026",
                "audience": "elementary",
            },
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
        queued_status = client.get(
            f"/api/v1/submission-jobs/{submission_id}", headers=headers
        )
        worker = QueueWorker(
            jobs,
            {
                "survey_response": lambda payload: store_survey_response(
                    payload, definitions, responses
                )
            },
        )
        assert asyncio.run(worker.process_one()) is True
        completed_status = client.get(
            f"/api/v1/submission-jobs/{submission_id}", headers=headers
        )

    assert submit.status_code == 202
    assert queued_status.json()["status"] == "queued"
    assert completed_status.json()["status"] == "completed"
    assert responses.responses[0].answers == {"q1": "응답"}


def test_survey_heartbeat_is_saved_for_authenticated_participant() -> None:
    sessions = InMemorySurveySessions()
    app = create_app(
        Settings(
            "test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60
        ),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=InMemoryDefinitions(),
        survey_response_repository=InMemoryResponses(),
        job_repository=InMemoryJobs(),
    )
    app.state.survey_session_repository = sessions
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={
                "phone": "01012345678",
                "password": "password-2026",
                "audience": "elementary",
            },
        )
        response = client.post(
            "/api/v1/participant/survey-sessions/heartbeat",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            json={
                "sessionId": "session-1",
                "surveyRound": 1,
                "surveyVersion": "2026-round-1-v1",
                "startedAt": "2026-09-21T00:00:00Z",
                "lastHeartbeatAt": "2026-09-21T00:00:30Z",
                "activeSeconds": 30,
                "resumeCount": 1,
                "currentPage": 2,
                "visitedPageCount": 2,
                "totalPageCount": 15,
                "navigationCount": 2,
            },
        )

    assert response.status_code == 200
    assert sessions.heartbeats[0][0] == "participant-1"
    assert sessions.heartbeats[0][1].active_seconds == 30


def test_submission_rejects_unknown_question_key(open_participation_policy) -> None:
    app = create_app(
        Settings(
            "test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60
        ),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=InMemoryDefinitions(),
        survey_response_repository=InMemoryResponses(),
        job_repository=InMemoryJobs(),
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={
                "phone": "01012345678",
                "password": "password-2026",
                "audience": "elementary",
            },
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


def test_submission_overwrites_profile_answers_from_participant_account(open_participation_policy) -> None:
    definitions = InMemoryDefinitions()
    definitions.definition = SurveyDefinition(
        surveyRound=1,
        surveyVersion="profile-v1",
        questions=[
            SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1),
            SurveyQuestion(key="demo.grade", csvColumn="학년", order=2),
            SurveyQuestion(key="demo.contact", csvColumn="연구 참여자 연락처", order=3),
            SurveyQuestion(
                key="demo.guardianContact", csvColumn="보호자 연락처", order=4
            ),
        ],
        createdAt=datetime.now(UTC),
    )
    jobs = InMemoryJobs()
    app = create_app(
        Settings(
            "test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60
        ),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=definitions,
        survey_response_repository=InMemoryResponses(),
        job_repository=jobs,
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/participant/login",
            json={
                "phone": "01012345678",
                "password": "password-2026",
                "audience": "elementary",
            },
        )
        response = client.post(
            "/api/v1/survey-responses",
            headers={"Authorization": f"Bearer {login.json()['accessToken']}"},
            json={
                "surveyRound": 1,
                "surveyVersion": "profile-v1",
                "answers": {
                    "q1": "응답",
                    "demo.grade": 9,
                    "demo.contact": {"demo.contact_phone": "01000000000"},
                    "demo.guardianContact": {
                        "demo.guardianContact_phone": "01011111111"
                    },
                },
                "submissionId": "browser-submission-profile",
            },
        )

    assert response.status_code == 202
    assert jobs.jobs[0].payload["answers"] == {
        "q1": "응답",
        "demo.grade": 1,
        "demo.contact": {"demo.contact_phone": "01012345678"},
        "demo.guardianContact": {"demo.guardianContact_phone": "01098765432"},
    }


def test_t2_preserves_reentered_demographics_but_enforces_account_phones() -> None:
    from app.schemas.survey import SurveySubmissionCreate
    from app.services.submissions import SurveySubmissionService

    definitions = InMemoryDefinitions()
    definitions.definition = SurveyDefinition(
        surveyRound=2,
        surveyVersion="t2-part1-v9-1022",
        audience="all",
        questions=[
            SurveyQuestion(key=key, csvColumn=key, order=order)
            for order, key in enumerate(
                ["demo.grade", "demo.contact", "demo.guardianContact"]
            )
        ],
        createdAt=datetime.now(UTC),
    )
    accounts = InMemoryParticipantAccounts()
    jobs = InMemoryJobs()
    asyncio.run(
        SurveySubmissionService(definitions, jobs).submit(
            accounts.account.participant_id,
            SurveySubmissionCreate(
                surveyRound=2,
                surveyVersion=definitions.definition.survey_version,
                submissionId="t2-reentry",
                answers={
                    "demo.grade": 9,
                    "demo.contact": {
                        "demo.contact_phone": "01000000000",
                        "demo.contact_email": "new@example.com",
                    },
                    "demo.guardianContact": {
                        "demo.guardianContact_phone": "01000000001"
                    },
                },
            ),
            accounts.account,
        )
    )

    assert jobs.jobs[0].payload["answers"] == {
        "demo.grade": 9,
        "demo.contact": {
            "demo.contact_phone": "01012345678",
            "demo.contact_email": "new@example.com",
        },
        "demo.guardianContact": {"demo.guardianContact_phone": "01098765432"},
    }
    assert accounts.account.grade == 4


def test_common_t2_registration_does_not_append_t1_followup() -> None:
    from app.schemas.survey import SurveyDefinitionCreate
    from scripts.register_frontend_surveys import build_payloads

    payload = build_payloads(
        {
            "common": [
                {
                    "surveyRound": 2,
                    "surveyVersion": "t2-part2-v9-1022",
                    "part": 2,
                    "scales": [
                        {
                            "scaleId": "followup",
                            "questions": [{"key": "followup.q1", "type": "single"}],
                        }
                    ],
                }
            ]
        },
        2,
    )[0]
    definition = SurveyDefinitionCreate.model_validate(payload)
    assert definition.audience == "all"
    assert [question.key for question in definition.questions] == ["followup.q1"]
    assert (
        SurveyDefinitionCreate.model_validate(
            {**payload, "audience": "common"}
        ).audience
        == "all"
    )


def test_partial_progress_revision_isolation_replacement_and_completion(open_participation_policy) -> None:
    from app.schemas.survey import SurveyProgressRecord
    from app.services.survey_progress import ProgressConflictError

    class MemoryProgress:
        def __init__(self):
            self.records = {}

        async def get(self, participant_id, survey_round, survey_version):
            return self.records.get((participant_id, survey_round, survey_version))

        async def save(self, participant_id, progress):
            key = (participant_id, progress.survey_round, progress.survey_version)
            previous = self.records.get(key)
            if progress.revision != (previous.revision if previous else 0):
                raise ProgressConflictError
            saved = SurveyProgressRecord(
                **progress.model_dump(exclude={"revision"}),
                revision=progress.revision + 1,
                updated_at=datetime.now(UTC),
            )
            self.records[key] = saved
            return saved

    definitions = InMemoryDefinitions()
    responses = InMemoryResponses()
    repository = MemoryProgress()
    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(
        Settings("test", None, "ai_us_test", (), secret),
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=definitions,
        survey_response_repository=responses,
        survey_progress_repository=repository,
    )
    headers = {
        "Authorization": "Bearer "
        + create_access_token("participant-1", "participant", secret, 60)
    }
    other_headers = {
        "Authorization": "Bearer "
        + create_access_token("other", "participant", secret, 60)
    }
    payload = {
        "surveyRound": 1,
        "surveyVersion": definitions.definition.survey_version,
        "revision": 0,
        "completedPage": 1,
        "nextPage": 2,
        "totalPages": 2,
        "answers": {"q1": "response"},
        "clientSavedAt": "2026-10-23T00:00:00Z",
    }
    query = f"/api/v1/survey-progress?surveyRound=1&surveyVersion={definitions.definition.survey_version}"
    with TestClient(app) as client:
        assert client.put("/api/v1/survey-progress", json=payload).status_code == 401
        assert client.get(query, headers=headers).status_code == 404
        saved = client.put("/api/v1/survey-progress", headers=headers, json=payload)
        assert saved.status_code == 200
        assert saved.json()["revision"] == 1
        assert client.get(query, headers=other_headers).status_code == 404
        assert (
            client.put(
                "/api/v1/survey-progress", headers=headers, json=payload
            ).status_code
            == 409
        )
        assert (
            client.get(
                "/api/v1/participant/survey-progress?survey_round=1", headers=headers
            ).json()["completedVersions"]
            == []
        )
        replaced = client.put(
            "/api/v1/survey-progress",
            headers=headers,
            json={**payload, "revision": 1, "answers": {}},
        )
        assert replaced.json()["revision"] == 2
        assert replaced.json()["answers"] == {}
        invalid = client.put(
            "/api/v1/survey-progress",
            headers=headers,
            json={**payload, "revision": 2, "answers": {"unknown": 1}},
        )
        assert invalid.status_code == 422
        responses.responses.append(
            SurveyResponseRecord(
                participantId="participant-1",
                surveyRound=1,
                surveyVersion=definitions.definition.survey_version,
                answers={"q1": "final"},
                submittedAt=datetime.now(UTC),
            )
        )
        assert (
            client.put(
                "/api/v1/survey-progress",
                headers=headers,
                json={**payload, "revision": 2},
            ).status_code
            == 409
        )


def test_final_response_retry_repairs_progress_without_replacing_answers() -> None:
    import pytest
    from app.services.surveys import MongoSurveyResponseRepository
    from pymongo.errors import DuplicateKeyError

    class Responses:
        document = None

        async def insert_one(self, document):
            if self.document is not None:
                raise DuplicateKeyError("already submitted")
            self.document = document

        async def find_one(self, query):
            return self.document

    class Progress:
        calls = 0
        completed = False

        async def update_one(self, query, update, **options):
            self.calls += 1
            assert options["upsert"] is True
            assert query["_id"] == "participant-1:2:t2-part1"
            if self.calls == 1:
                raise RuntimeError("transient progress write failure")
            self.completed = update["$set"]["completed"]

    responses, progress = Responses(), Progress()
    repository = MongoSurveyResponseRepository(responses, progress)
    record = SurveyResponseRecord(
        participantId="participant-1",
        surveyRound=2,
        surveyVersion="t2-part1",
        answers={"q1": "original"},
        submittedAt=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(repository.create_response(record))
    asyncio.run(repository.create_response(record))
    assert progress.completed is True
    with pytest.raises(DuplicateKeyError):
        asyncio.run(
            repository.create_response(
                record.model_copy(update={"answers": {"q1": "different"}})
            )
        )
    assert responses.document["answers"] == {"q1": "original"}


def test_retired_t4_blocks_new_writes_preserves_existing_exports():
    import csv
    import io
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.schemas.survey import SurveyDefinitionSummary
    from app.services.application_settings import default_survey_rounds

    class Definitions(InMemoryDefinitions):
        async def list_definitions(self):
            return [SurveyDefinitionSummary(surveyRound=4, surveyVersion=f"legacy-part{part}",
                audience="all", part=part, questionCount=1, createdAt=datetime.now(UTC)) for part in [1, 2]]

        async def get(self, survey_round, survey_version):
            if survey_round == 4 and survey_version in {"legacy-part1", "legacy-part2"}:
                return self.definition.model_copy(update={"survey_round": 4, "survey_version": survey_version,
                    "spec": {"part": int(survey_version[-1])}})
            return None

    records = default_survey_rounds(True)
    records["4"].update(is_open=True, configuration_id="legacy-parts", period_ended=False)
    settings = SimpleNamespace(participation_settings=AsyncMock(return_value={"participation_paused": False}),
        survey_rounds=AsyncMock(return_value=records), update_survey_round=AsyncMock())
    accounts, responses, jobs = InMemoryParticipantAccounts(), InMemoryResponses(), InMemoryJobs()
    accounts.list_participants = AsyncMock(return_value=[accounts.account])
    responses.responses.append(SurveyResponseRecord(participantId="participant-1", surveyRound=4,
        surveyVersion="legacy-part2", answers={"q1": "legacy-answer"}, submittedAt=datetime.now(UTC)))
    progress = SimpleNamespace(get=AsyncMock(return_value=None), save=AsyncMock())
    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(Settings("test", None, "ai_us_test", (), secret),
        application_settings_repository=settings, participant_account_repository=accounts,
        survey_definition_repository=Definitions(), survey_response_repository=responses,
        survey_progress_repository=progress, job_repository=jobs)
    participant = {"Authorization": "Bearer " + create_access_token("participant-1", "participant", secret, 60)}
    admin = {"Authorization": "Bearer " + create_access_token("admin-1", "admin", secret, 60)}
    with TestClient(app) as client:
        status = client.get("/api/v1/participant/survey-status", headers=participant).json()
        assert status["surveyRoundsOpen"]["4"] is False
        assert status["surveyRoundStates"]["4"] == "closed"
        assert 4 in status["completedRounds"]
        assert client.get("/api/v1/participant/survey-configurations/4", headers=participant).json() == []
        assert client.post("/api/v1/survey-responses", headers=participant, json={"surveyRound": 4,
            "surveyVersion": "legacy-part1", "answers": {"q1": "new"}, "submissionId": "retired"}).status_code == 403
        assert client.put("/api/v1/survey-progress", headers=participant, json={"surveyRound": 4,
            "surveyVersion": "legacy-part1", "answers": {"q1": "new"}, "revision": 0,
            "completedPage": 0, "nextPage": 1, "totalPages": 2,
            "clientSavedAt": datetime.now(UTC).isoformat()}).status_code == 403
        assert client.put("/api/v1/admin/survey-round-settings/4", headers=admin, json={"isOpen": True}).status_code == 410
        exported = client.get("/api/v1/researcher/exports/survey-responses?survey_round=4&survey_version=legacy-part2", headers=admin)
        assert exported.status_code == 200
        rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
        assert len(rows) == 2
        assert rows[1][rows[0].index("첫 번째 문항")] == "legacy-answer"
    settings.update_survey_round.assert_not_called()
    progress.save.assert_not_called()
    assert not jobs.jobs
    assert records["4"]["is_open"] is True
    assert len(responses.responses) == 1


def test_followup_allowlist_deadline_and_t3_without_t2(monkeypatch):
    import csv
    import io
    from datetime import timedelta
    import app.services.application_settings as policy
    from app.schemas.survey import SurveyDefinitionSummary, SurveyProgressRecord

    class SettingsRepository:
        records = {
            "1": {"configuration_id": "parts", "is_open": False, "period_ended": True},
            "2": {"configuration_id": "parts", "is_open": False, "period_ended": True},
            "3": {"configuration_id": "parts", "is_open": True, "period_ended": False},
            "4": {"is_open": False},
        }

        async def participation_settings(self):
            return {"participation_paused": True}

        async def survey_rounds(self, t1_open):
            return self.records

        async def update_survey_round(self, survey_round, changes, updated_by):
            self.records[str(survey_round)].update(changes)

    class Definitions(InMemoryDefinitions):
        async def list_definitions(self):
            return [SurveyDefinitionSummary(
                surveyRound=round_number, surveyVersion=f"part{part}", audience="all",
                part=part, questionCount=1, createdAt=datetime.now(UTC),
            ) for round_number in range(1, 5) for part in [1, 2]]

        async def get(self, survey_round, survey_version):
            if survey_version not in {"part1", "part2"}:
                return None
            return self.definition.model_copy(update={
                "survey_round": survey_round, "survey_version": survey_version,
                "spec": {"part": int(survey_version[-1])},
            })

    class Progress:
        async def get(self, participant_id, survey_round, survey_version):
            return None

        async def save(self, participant_id, progress):
            return SurveyProgressRecord(**progress.model_dump(exclude={"revision"}), revision=1, updatedAt=datetime.now(UTC))

    class Accounts(InMemoryParticipantAccounts):
        async def list_participants(self):
            return [self.account]

    class Responses(InMemoryResponses):
        async def list_responses(self, survey_round, survey_version):
            return [response for response in self.responses
                    if response.survey_round == survey_round and response.survey_version == survey_version]

    instant = policy.T2_FOLLOWUP_DEADLINE - timedelta(microseconds=1)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(policy, "datetime", Clock)
    settings_repository, responses, jobs = SettingsRepository(), Responses(), InMemoryJobs()
    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(
        Settings("test", None, "ai_us_test", (), secret),
        application_settings_repository=settings_repository,
        participant_account_repository=Accounts(),
        survey_definition_repository=Definitions(), survey_response_repository=responses,
        survey_progress_repository=Progress(), job_repository=jobs,
    )
    headers = {"Authorization": "Bearer " + create_access_token("participant-1", "participant", secret, 60)}
    admin = {"Authorization": "Bearer " + create_access_token("admin-1", "admin", secret, 60)}
    status_path = "/api/v1/participant/survey-status"
    payload = {"surveyRound": 2, "surveyVersion": "part1", "answers": {"q1": "answer"}, "submissionId": "followup"}
    draft = {"surveyRound": 2, "surveyVersion": "part1", "answers": {"q1": "answer"}, "revision": 0,
             "completedPage": 0, "nextPage": 1, "totalPages": 2, "clientSavedAt": datetime.now(UTC).isoformat()}
    with TestClient(app) as client:
        initial = client.get(status_path, headers=headers).json()
        assert initial["completedRounds"] == []
        assert initial["surveyRoundsOpen"] == {"1": False, "2": False, "3": True, "4": False}
        assert client.get("/api/v1/participant/survey-configurations/2", headers=headers).json() == []
        assert client.post("/api/v1/survey-responses", headers=headers, json=payload).status_code == 403
        assert client.put("/api/v1/survey-progress", headers=headers, json=draft).status_code == 403
        assert client.post("/api/v1/survey-responses", headers=headers, json={**payload, "surveyRound": 3, "surveyVersion": "part2"}).status_code == 403
        assert client.post("/api/v1/survey-responses", headers=headers, json={**payload, "surveyRound": 3, "submissionId": "t3-no-t2"}).status_code == 202
        route = "/api/v1/admin/survey-round-settings/2"
        assert client.put(route, headers=headers, json={"followupParticipantIds": ["participant-1"]}).status_code == 403
        updated = client.put(route, headers=admin, json={"followupParticipantIds": ["participant-1", "participant-1"]})
        assert updated.status_code == 200
        assert updated.json()["followupParticipantCount"] == 1
        assert updated.json()["periodEnded"] is True and updated.json()["isOpen"] is False
        assert client.get(status_path, headers=headers).json()["surveyRoundsOpen"]["2"] is True
        assert len(client.get("/api/v1/participant/survey-configurations/2", headers=headers).json()) == 2
        assert client.put("/api/v1/survey-progress", headers=headers, json=draft).status_code == 200
        assert client.post("/api/v1/survey-responses", headers=headers, json=payload).status_code == 202
        worker = QueueWorker(jobs, {"survey_response": lambda queued: store_survey_response(
            queued, app.state.survey_definition_repository, responses)})
        assert asyncio.run(worker.process_one())
        assert asyncio.run(worker.process_one())
        assert client.post("/api/v1/survey-responses", headers=headers, json={
            **payload, "surveyVersion": "part2", "answers": {"q1": "allowlisted-answer"},
            "submissionId": "followup-part2",
        }).status_code == 202
        assert asyncio.run(worker.process_one())
        export_path = "/api/v1/researcher/exports/survey-responses?survey_round=2&survey_version=part2"
        for suffix, expected_rows in [("", 1), ("&school_level=초등", 1), ("&school_level=중등", 0)]:
            exported = client.get(export_path + suffix, headers=admin)
            assert exported.status_code == 200
            rows = list(csv.reader(io.StringIO(exported.text.lstrip("\ufeff"))))
            assert len(rows) == expected_rows + 1
            if expected_rows:
                assert rows[1][rows[0].index("첫 번째 문항")] == "allowlisted-answer"
        assert client.get(export_path, headers=headers).status_code == 403
        for instant in [policy.T2_FOLLOWUP_DEADLINE, policy.T2_FOLLOWUP_DEADLINE + timedelta(seconds=1)]:
            assert client.get(status_path, headers=headers).json()["surveyRoundsOpen"]["2"] is False
            assert client.get("/api/v1/participant/survey-configurations/2", headers=headers).json() == []
            assert client.put("/api/v1/survey-progress", headers=headers, json=draft).status_code == 403
            assert client.post("/api/v1/survey-responses", headers=headers, json={**payload, "submissionId": "late"}).status_code == 403
        assert settings_repository.records["2"]["period_ended"] is True
        assert settings_repository.records["4"]["is_open"] is False


@pytest.mark.parametrize("t1_fallback", [False, True])
def test_round_admission_blocks_writes_preserves_reads_and_finishes_queued_jobs(
    t1_fallback,
):
    from app.schemas.survey import SurveyProgressRecord
    from app.services.application_settings import default_survey_rounds

    class RoundSettings:
        async def participation_settings(self):
            return {"participation_paused": False}

        def __init__(self):
            self.records = {
                "1": {
                    "configuration_id": "parts",
                    "is_open": t1_fallback,
                    "period_ended": False,
                },
                "2": {
                    "configuration_id": "parts",
                    "is_open": True,
                    "period_ended": False,
                },
            }

        async def survey_rounds(self, t1_open):
            return {**default_survey_rounds(t1_open), **self.records}

        async def set_survey_round_open(self, survey_round, enabled, updated_by):
            self.records[str(survey_round)] = {
                "is_open": enabled,
                "updated_by": updated_by,
                "updated_at": datetime.now(UTC),
            }

        async def update_survey_round(self, survey_round, changes, updated_by):
            self.records.setdefault(str(survey_round), {"is_open": False}).update(
                {
                    **changes,
                    "updated_by": updated_by,
                    "updated_at": datetime.now(UTC),
                }
            )

    class Definitions(InMemoryDefinitions):
        async def list_definitions(self):
            from app.schemas.survey import SurveyDefinitionSummary

            return [
                SurveyDefinitionSummary(
                    surveyRound=round_number,
                    surveyVersion=f"part{part}",
                    audience="all",
                    part=part,
                    questionCount=1,
                    createdAt=datetime.now(UTC),
                )
                for round_number in range(1, 5)
                for part in [1, 2]
            ]

        async def get(self, survey_round, survey_version):
            if survey_round in range(1, 5) and survey_version in {"part1", "part2"}:
                return self.definition.model_copy(
                    update={
                        "survey_round": survey_round,
                        "survey_version": survey_version,
                        "spec": {"part": int(survey_version[-1])},
                    }
                )
            return None

    class Progress:
        record = None

        async def get(self, participant_id, survey_round, survey_version):
            return self.record

        async def save(self, participant_id, progress):
            self.record = SurveyProgressRecord(
                **progress.model_dump(exclude={"revision"}),
                revision=progress.revision + 1,
                updated_at=datetime.now(UTC),
            )
            return self.record

    settings_repository, definitions = RoundSettings(), Definitions()
    responses, jobs, progress = InMemoryResponses(), InMemoryJobs(), Progress()
    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(
        Settings(
            "test", None, "ai_us_test", (), secret, t1_submissions_open=t1_fallback
        ),
        application_settings_repository=settings_repository,
        participant_account_repository=InMemoryParticipantAccounts(),
        survey_definition_repository=definitions,
        survey_response_repository=responses,
        survey_progress_repository=progress,
        job_repository=jobs,
    )
    headers = {
        "Authorization": "Bearer "
        + create_access_token("participant-1", "participant", secret, 60)
    }
    admin = {
        "Authorization": "Bearer " + create_access_token("admin-1", "admin", secret, 60)
    }
    submission = {
        "surveyRound": 2,
        "surveyVersion": "part1",
        "answers": {"q1": "original"},
        "submissionId": "accepted-before-close",
    }
    draft = {
        "surveyRound": 2,
        "surveyVersion": "part1",
        "answers": {"q1": "draft"},
        "revision": 0,
        "completedPage": 1,
        "nextPage": 2,
        "totalPages": 2,
        "clientSavedAt": "2026-10-23T00:00:00Z",
    }
    with TestClient(app) as client:
        status_path = "/api/v1/participant/survey-status"
        initial = client.get(status_path, headers=headers).json()
        assert initial["surveyRoundsOpen"] == {
            "1": t1_fallback,
            "2": True,
            "3": False,
            "4": False,
        }
        selected = client.get(
            "/api/v1/participant/survey-configurations/2", headers=headers
        )
        assert selected.status_code == 200
        assert [item["surveyVersion"] for item in selected.json()] == ["part1", "part2"]
        assert (
            client.get(
                "/api/v1/participant/survey-configurations/3", headers=headers
            ).json()
            == []
        )
        assert (
            client.get("/api/v1/participant/survey-configurations/2").status_code == 401
        )
        assert initial["surveyRoundStates"]["2"] == "open"
        assert (
            client.post(
                "/api/v1/survey-responses",
                headers=headers,
                json={**submission, "surveyVersion": "unselected"},
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/v1/survey-progress", headers=headers, json=draft
            ).status_code
            == 200
        )
        accepted = client.post(
            "/api/v1/survey-responses", headers=headers, json=submission
        )
        assert accepted.status_code == 202
        assert (
            client.put(
                "/api/v1/admin/survey-round-settings/2",
                headers=admin,
                json={"isOpen": False},
            ).status_code
            == 200
        )
        assert (
            client.get(status_path, headers=headers).json()["surveyRoundsOpen"]["2"]
            is False
        )
        before = progress.record.model_dump()
        assert (
            client.put(
                "/api/v1/survey-progress",
                headers=headers,
                json={**draft, "revision": 1, "answers": {"q1": "blocked"}},
            ).status_code
            == 403
        )
        assert progress.record.model_dump() == before
        assert (
            client.post(
                "/api/v1/survey-responses",
                headers=headers,
                json={**submission, "submissionId": "blocked-after-close"},
            ).status_code
            == 403
        )
        assert len(jobs.jobs) == 1
        saved = client.get(
            "/api/v1/survey-progress?surveyRound=2&surveyVersion=part1", headers=headers
        )
        assert saved.status_code == 200 and saved.json()["answers"] == {"q1": "draft"}
        worker = QueueWorker(
            jobs,
            {
                "survey_response": lambda payload: store_survey_response(
                    payload, definitions, responses
                )
            },
        )
        assert asyncio.run(worker.process_one()) is True
        assert (
            client.get(
                "/api/v1/submission-jobs/" + accepted.json()["submissionId"],
                headers=headers,
            ).json()["status"]
            == "completed"
        )
        assert responses.responses[0].answers == {"q1": "original"}
        assert client.get(status_path, headers=headers).json()[
            "completedVersionsByRound"
        ]["2"] == ["part1"]
        assert (
            client.get(
                "/api/v1/participant/survey-progress?survey_round=2", headers=headers
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/v1/admin/survey-round-settings/2",
                headers=admin,
                json={"isOpen": True},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/v1/survey-progress",
                headers=headers,
                json={**draft, "revision": 1},
            ).status_code
            == 409
        )
        assert (
            client.put(
                "/api/v1/survey-progress",
                headers=headers,
                json={**draft, "surveyVersion": "part2"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/survey-responses",
                headers=headers,
                json={
                    **submission,
                    "surveyVersion": "part2",
                    "submissionId": "reopened-part2",
                },
            ).status_code
            == 202
        )
        for enabled in [True, False]:
            assert (
                client.put(
                    "/api/v1/admin/survey-round-settings/1",
                    headers=admin,
                    json={"isOpen": enabled},
                ).status_code
                == 200
            )
            status = client.get(status_path, headers=headers).json()
            assert status["t1SubmissionsOpen"] is enabled
            assert status["surveyRoundsOpen"]["1"] is enabled
            assert client.post(
                "/api/v1/survey-responses",
                headers=headers,
                json={**submission, "surveyRound": 1, "submissionId": "t1-override"},
            ).status_code == (202 if enabled else 403)
        for survey_round in [3, 4]:
            assert (
                client.post(
                    "/api/v1/survey-responses",
                    headers=headers,
                    json={**submission, "surveyRound": survey_round},
                ).status_code
                == 403
            )
        assert (
            client.put(
                "/api/v1/admin/survey-round-settings/3",
                headers=admin,
                json={"configurationId": "parts"},
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/v1/admin/survey-round-settings/3",
                headers=admin,
                json={"isOpen": True},
            ).status_code
            == 200
        )
        status = client.get(status_path, headers=headers).json()
        assert status["surveyRoundsOpen"] == {
            "1": False,
            "2": True,
            "3": True,
            "4": False,
        }
        assert (
            client.post(
                "/api/v1/survey-responses",
                headers=headers,
                json={**submission, "surveyRound": 3, "submissionId": "t3-open"},
            ).status_code
            == 202
        )


def test_t2_first_part_order_completion_and_chat_gate_without_t1(open_participation_policy) -> None:
    from dataclasses import replace

    class Definitions(InMemoryDefinitions):
        async def get(self, survey_round, survey_version):
            if survey_round in {1, 2} and survey_version in {"part1", "part2"}:
                return self.definition.model_copy(
                    update={
                        "survey_round": survey_round,
                        "survey_version": survey_version,
                        "spec": {"part": int(survey_version[-1])},
                    }
                )
            return None

    definitions, responses, jobs = Definitions(), InMemoryResponses(), InMemoryJobs()
    participants = InMemoryParticipantAccounts()
    participants.account = replace(participants.account, chat_consent=True)
    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(
        Settings("test", None, "ai_us_test", (), secret, t1_submissions_open=False),
        participant_account_repository=participants,
        survey_definition_repository=definitions,
        survey_response_repository=responses,
        job_repository=jobs,
    )
    headers = {
        "Authorization": "Bearer "
        + create_access_token("participant-1", "participant", secret, 60)
    }
    payload = {
        "surveyRound": 2,
        "surveyVersion": "part2",
        "answers": {"q1": "answer"},
        "submissionId": "second-before-first",
    }
    chat = {
        "submissionPoint": "afterRound2",
        "sourceType": "text",
        "rawInput": "synthetic test",
        "submissionId": "chat-t2",
    }
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/survey-responses", json=payload, headers=headers
            ).status_code
            == 403
        )
        assert jobs.jobs == []
        assert (
            client.post(
                "/api/v1/chat-submissions", json=chat, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/survey-responses",
                json={
                    **payload,
                    "surveyVersion": "part1",
                    "submissionId": "queued-part1",
                },
                headers=headers,
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/api/v1/survey-responses", json=payload, headers=headers
            ).status_code
            == 403
        )
        responses.responses.append(
            SurveyResponseRecord(
                participantId="participant-1",
                surveyRound=2,
                surveyVersion="part1",
                answers={"q1": "first"},
                submittedAt=datetime.now(UTC),
            )
        )
        assert (
            client.get("/api/v1/participant/survey-status", headers=headers).json()[
                "completedRounds"
            ]
            == []
        )
        assert (
            client.post(
                "/api/v1/chat-submissions", json=chat, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/survey-responses", json=payload, headers=headers
            ).status_code
            == 202
        )
        responses.responses.append(
            SurveyResponseRecord(
                participantId="participant-1",
                surveyRound=2,
                surveyVersion="part2",
                answers={"q1": "second"},
                submittedAt=datetime.now(UTC),
            )
        )
        result = client.get("/api/v1/participant/survey-status", headers=headers).json()
        assert result["completedRounds"] == [2]
        assert result["completedVersionsByRound"]["1"] == []
        assert result["t1SubmissionsOpen"] is False
        assert (
            client.post(
                "/api/v1/chat-submissions", json=chat, headers=headers
            ).status_code
            == 202
        )
        participants.account = replace(participants.account, chat_consent=False)
        assert (
            client.post(
                "/api/v1/chat-submissions", json=chat, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/survey-responses",
                json={**payload, "surveyRound": 1},
                headers=headers,
            ).status_code
            == 403
        )
        assert (
            client.get(
                "/api/v1/participant/survey-progress?survey_round=1", headers=headers
            ).status_code
            == 200
        )
