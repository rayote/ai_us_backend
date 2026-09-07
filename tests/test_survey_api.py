from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.survey import SurveyDefinition, SurveyDefinitionCreate, SurveyResponseRecord
from app.services.auth import (
    ParticipantAccount,
    ParticipantAccountRepository,
    ResearcherAccount,
    ResearcherAccountRepository,
)
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository
from fastapi.testclient import TestClient


class InMemoryResearchers(ResearcherAccountRepository):
    def __init__(self) -> None:
        self.accounts = {
            "admin": ResearcherAccount("admin-1", "admin", hash_password("admin-password"), "admin"),
            "researcher": ResearcherAccount(
                "researcher-1", "researcher", hash_password("researcher-password"), "researcher"
            ),
        }

    async def find_by_username(self, username: str) -> ResearcherAccount | None:
        return self.accounts.get(username)

    async def ensure_bootstrap(self, username: str, password_hash: str) -> None:
        return None

    async def create(self, username: str, password_hash: str, role: str) -> str | None:
        return None


class InMemorySurveyDefinitions(SurveyDefinitionRepository):
    def __init__(self) -> None:
        self.definitions: dict[tuple[int, str], SurveyDefinition] = {}

    async def create(self, definition: SurveyDefinitionCreate) -> SurveyDefinition:
        created = SurveyDefinition(
            surveyRound=definition.survey_round,
            surveyVersion=definition.survey_version,
            questions=definition.questions,
            createdAt=datetime.now(UTC),
        )
        self.definitions[(created.survey_round, created.survey_version)] = created
        return created

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None:
        return self.definitions.get((survey_round, survey_version))


class InMemorySurveyResponses(SurveyResponseRepository):
    async def create_response(self, response: SurveyResponseRecord) -> None:
        return None

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]:
        return [
            SurveyResponseRecord(
                participantId="participant-1",
                surveyRound=survey_round,
                surveyVersion=survey_version,
                answers={"q1": "응답"},
                submittedAt=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
            )
        ]


class InMemoryParticipants(ParticipantAccountRepository):
    async def find_by_phone(self, phone: str):
        return None

    async def find_by_id(self, participant_id: str):
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(
        self, phone: str, password_hash: str, chat_consent: bool = False, school_level: str | None = None
    ) -> bool:
        return False

    async def create_imported(self, phone: str, password_hash: str, name: str, school_level: str, grade: int) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return [ParticipantAccount("participant-1", "01012345678", "hash", False, school_level="초등")]


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
            researcher_account_repository=InMemoryResearchers(),
            participant_account_repository=InMemoryParticipants(),
            survey_definition_repository=InMemorySurveyDefinitions(),
            survey_response_repository=InMemorySurveyResponses(),
        )
    )


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/researcher/login", json={"username": username, "password": password})
    return response.json()["accessToken"]


def _definition_payload() -> dict[str, object]:
    return {
        "surveyRound": 2,
        "surveyVersion": "2026-round-2-v2",
        "questions": [{"key": "q1", "csvColumn": "첫 번째 문항", "order": 1}],
    }


def test_admin_registers_definition_and_researcher_downloads_csv() -> None:
    with _client() as client:
        admin_token = _token(client, "admin", "admin-password")
        create_response = client.post(
            "/api/v1/admin/survey-definitions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=_definition_payload(),
        )
        researcher_token = _token(client, "researcher", "researcher-password")
        export_response = client.get(
            "/api/v1/researcher/exports/survey-responses",
            headers={"Authorization": f"Bearer {researcher_token}"},
            params={"survey_round": 2, "survey_version": "2026-round-2-v2"},
        )

    assert create_response.status_code == 201
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/csv")
    assert "아이디(휴대폰),학교급,학년,surveyRound,surveyVersion,submittedAt,첫 번째 문항" in export_response.text
    assert "01012345678,초등" in export_response.text


def test_researcher_cannot_register_survey_definition() -> None:
    with _client() as client:
        researcher_token = _token(client, "researcher", "researcher-password")
        response = client.post(
            "/api/v1/admin/survey-definitions",
            headers={"Authorization": f"Bearer {researcher_token}"},
            json=_definition_payload(),
        )

    assert response.status_code == 403


def test_researcher_can_preview_survey_responses() -> None:
    with _client() as client:
        researcher_token = _token(client, "researcher", "researcher-password")
        response = client.get(
            "/api/v1/researcher/survey-response-previews",
            headers={"Authorization": f"Bearer {researcher_token}"},
            params={"survey_round": 2, "survey_version": "2026-round-2-v2"},
        )

    assert response.status_code == 200
    assert response.json()[0]["phone"] == "01012345678"
    assert response.json()[0]["schoolLevel"] == "초등"
