from datetime import UTC, datetime

from app.core.security import hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.survey import SurveyDefinition, SurveyDefinitionCreate, SurveyDefinitionSummary, SurveyResponseRecord
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
            audience=definition.audience,
            questions=definition.questions,
            spec=definition.raw_spec,
            createdAt=datetime.now(UTC),
        )
        self.definitions[(created.survey_round, created.survey_version)] = created
        return created

    async def replace(self, definition: SurveyDefinitionCreate) -> SurveyDefinition:
        replaced = SurveyDefinition(
            surveyRound=definition.survey_round,
            surveyVersion=definition.survey_version,
            audience=definition.audience,
            questions=definition.questions,
            spec=definition.raw_spec,
            createdAt=datetime.now(UTC),
        )
        self.definitions[(replaced.survey_round, replaced.survey_version)] = replaced
        return replaced

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None:
        return self.definitions.get((survey_round, survey_version))

    async def list_definitions(self) -> list[SurveyDefinitionSummary]:
        return [
            SurveyDefinitionSummary(
                surveyRound=definition.survey_round,
                surveyVersion=definition.survey_version,
                audience=definition.audience,
                part=definition.spec.get("part") if definition.spec else None,
                title=definition.spec.get("_meta", {}).get("title") if definition.spec else None,
                questionCount=len(definition.questions),
                createdAt=definition.created_at,
            )
            for definition in self.definitions.values()
        ]


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
    assert '"=""01012345678""",초등' in export_response.text


def test_researcher_cannot_register_survey_definition() -> None:
    with _client() as client:
        researcher_token = _token(client, "researcher", "researcher-password")
        response = client.post(
            "/api/v1/admin/survey-definitions",
            headers={"Authorization": f"Bearer {researcher_token}"},
            json=_definition_payload(),
        )

    assert response.status_code == 403


def test_admin_can_replace_existing_survey_definition() -> None:
    with _client() as client:
        admin_token = _token(client, "admin", "admin-password")
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post("/api/v1/admin/survey-definitions", headers=headers, json=_definition_payload())

        updated_payload = dict(_definition_payload())
        updated_payload["questions"] = [
            {"key": "q1", "csvColumn": "첫 번째 문항(수정)", "order": 1},
            {"key": "q2", "csvColumn": "두 번째 문항", "order": 2},
        ]
        response = client.put(
            "/api/v1/admin/survey-definitions/2/2026-round-2-v2",
            headers=headers,
            json=updated_payload,
        )

    assert response.status_code == 200
    assert [question["key"] for question in response.json()["questions"]] == ["q1", "q2"]


def test_admin_replace_rejects_mismatched_path_and_body() -> None:
    with _client() as client:
        admin_token = _token(client, "admin", "admin-password")
        response = client.put(
            "/api/v1/admin/survey-definitions/999/other-version",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=_definition_payload(),
        )

    assert response.status_code == 400


def test_admin_registers_nested_scale_survey_definition() -> None:
    with _client() as client:
        admin_token = _token(client, "admin", "admin-password")
        response = client.post(
            "/api/v1/admin/survey-definitions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "surveyRound": 1,
                "audience": "elementary",
                "surveyVersion": "t1-elem-v1-draft",
                "scales": [
                    {
                        "scaleId": "motive",
                        "order": 4,
                        "scaleName": "AI 활용동기",
                        "questions": [
                            {
                                "key": "motive.q1",
                                "no": "1",
                                "text": "나는 가족, 친구, 다른 문제에서 벗어나려고 AI를 사용한다.",
                                "type": "likert",
                                "required": True,
                            },
                            {
                                "key": "demo.contact",
                                "no": "2",
                                "text": "연구 참여자 연락처",
                                "type": "composite",
                                "fields": [
                                    {"key": "demo.contact_phone", "label": "휴대폰 번호", "type": "tel"},
                                    {"key": "demo.contact_email", "label": "이메일", "type": "text"},
                                ],
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 201
    assert response.json()["audience"] == "elementary"
    assert response.json()["questions"] == [
        {
            "key": "motive.q1",
            "csvColumn": "AI 활용동기 | 1 | 나는 가족, 친구, 다른 문제에서 벗어나려고 AI를 사용한다.",
            "order": 1,
        },
        {"key": "demo.contact", "csvColumn": "AI 활용동기 | 2 | 연구 참여자 연락처", "order": 2},
        {"key": "demo.contact_phone", "csvColumn": "AI 활용동기 | 2 | 연구 참여자 연락처 | 휴대폰 번호", "order": 3},
        {"key": "demo.contact_email", "csvColumn": "AI 활용동기 | 2 | 연구 참여자 연락처 | 이메일", "order": 4},
    ]
    assert response.json()["spec"]["scales"][0]["scaleId"] == "motive"


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


def test_researcher_can_list_survey_definitions() -> None:
    with _client() as client:
        admin_token = _token(client, "admin", "admin-password")
        client.post(
            "/api/v1/admin/survey-definitions",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "surveyRound": 1,
                "surveyVersion": "t1-elem-part1-v1-draft",
                "audience": "elementary",
                "part": 1,
                "_meta": {"title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)"},
                "questions": [{"key": "q1", "csvColumn": "첫 번째 문항", "order": 1}],
            },
        )
        researcher_token = _token(client, "researcher", "researcher-password")
        response = client.get(
            "/api/v1/researcher/survey-definitions",
            headers={"Authorization": f"Bearer {researcher_token}"},
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "surveyRound": 1,
            "surveyVersion": "t1-elem-part1-v1-draft",
            "audience": "elementary",
            "part": 1,
            "title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)",
            "questionCount": 1,
            "createdAt": response.json()[0]["createdAt"],
        }
    ]
