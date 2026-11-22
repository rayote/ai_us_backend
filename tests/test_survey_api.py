import csv
import io
from datetime import UTC, datetime

import pytest

from app.api.researcher import _survey_export_filename
from app.core.security import create_access_token, hash_password
from app.core.settings import Settings
from app.main import create_app
from app.schemas.survey import (
    SurveyDefinition,
    SurveyDefinitionCreate,
    SurveyDefinitionSummary,
    SurveyQuestion,
    SurveyResponseRecord,
)
from app.services.auth import (
    ParticipantAccount,
    ParticipantAccountRepository,
    ResearcherAccount,
    ResearcherAccountRepository,
)
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository
from app.services.t3_export import FLAG_COLUMNS, T3_VERSIONS, _flags, _visible_answers
from fastapi.testclient import TestClient


class InMemoryResearchers(ResearcherAccountRepository):
    def __init__(self) -> None:
        self.accounts = {
            "admin": ResearcherAccount(
                "admin-1", "admin", hash_password("admin-password"), "admin"
            ),
            "researcher": ResearcherAccount(
                "researcher-1",
                "researcher",
                hash_password("researcher-password"),
                "researcher",
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

    async def get(
        self, survey_round: int, survey_version: str
    ) -> SurveyDefinition | None:
        return self.definitions.get((survey_round, survey_version))

    async def list_definitions(self) -> list[SurveyDefinitionSummary]:
        return [
            SurveyDefinitionSummary(
                surveyRound=definition.survey_round,
                surveyVersion=definition.survey_version,
                audience=definition.audience,
                part=definition.spec.get("part") if definition.spec else None,
                title=(
                    definition.spec.get("_meta", {}).get("title")
                    if definition.spec
                    else None
                ),
                questionCount=len(definition.questions),
                createdAt=definition.created_at,
            )
            for definition in self.definitions.values()
        ]


class InMemorySurveyResponses(SurveyResponseRepository):
    async def create_response(self, response: SurveyResponseRecord) -> None:
        return None

    async def list_responses(
        self, survey_round: int, survey_version: str
    ) -> list[SurveyResponseRecord]:
        return [
            SurveyResponseRecord(
                participantId="participant-1",
                surveyRound=survey_round,
                surveyVersion=survey_version,
                answers={"q1": "응답"},
                submittedAt=datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
            )
        ]

    async def list_versions_for_participant(
        self, participant_id: str, survey_round: int
    ) -> list[str]:
        return (
            ["t1-elem-part1-v2-0916"]
            if participant_id == "participant-1" and survey_round == 1
            else []
        )


class InMemoryParticipants(ParticipantAccountRepository):
    async def find_by_phone(self, phone: str):
        return None

    async def find_by_id(self, participant_id: str):
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(
        self,
        phone: str,
        password_hash: str,
        chat_consent: bool = False,
        school_level: str | None = None,
    ) -> bool:
        return False

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int
    ) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return [
            ParticipantAccount(
                "participant-1", "01012345678", "hash", False, school_level="초등"
            )
        ]


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings(
                "test",
                None,
                "ai_us_test",
                ("https://frontend.example.test",),
                "test-secret-at-least-thirty-two-bytes",
                60,
            ),
            researcher_account_repository=InMemoryResearchers(),
            participant_account_repository=InMemoryParticipants(),
            survey_definition_repository=InMemorySurveyDefinitions(),
            survey_response_repository=InMemorySurveyResponses(),
        )
    )


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/researcher/login",
        json={"username": username, "password": password},
    )
    return response.json()["accessToken"]


def _participant_token() -> str:
    return create_access_token(
        "participant-1", "participant", "test-secret-at-least-thirty-two-bytes", 60
    )


@pytest.fixture
def survey_client():
    with _client() as client:
        yield client


def test_researcher_t3_export_pairs_only_matching_final_parts(survey_client):
    client = survey_client
    timestamp = datetime(2026, 11, 22, 12, tzinfo=UTC)
    records = {
        T3_VERSIONS[0]: [("finished", "12"), ("draft", "11"), ("not-started", "10"), ("tester", "test-only")],
        T3_VERSIONS[1]: [("finished", "15"), ("orphan", "13"), ("tester", "test-only")],
        T3_VERSIONS[2]: [("secondary", "17")],
        T3_VERSIONS[3]: [("secondary", "17"), ("draft", "wrong-audience")],
    }

    class Responses:
        async def list_responses(self, round_number, version):
            assert round_number == 3
            return [SurveyResponseRecord(participantId=person, surveyRound=3, surveyVersion=version,
                                         submittedAt=timestamp, answers={"demo.age": age},
                                         detail={"testerSubmission": True} if person == "tester" else None)
                    for person, age in records[version]]

    class Progress:
        async def list_export_metadata(self, round_number, versions, participants):
            assert round_number == 3 and tuple(versions) == T3_VERSIONS
            assert participants == ["draft", "finished", "not-started", "secondary"]
            return [{"participant_id": "draft", "survey_version": T3_VERSIONS[1],
                     "answers": {"demo.age": "private-draft"},
                     "page_history": [{"page": 1, "saved_at": timestamp}]}]

    client.app.state.survey_response_repository = Responses()
    client.app.state.survey_progress_repository = Progress()
    for version in T3_VERSIONS:
        client.app.state.survey_definition_repository.definitions[3, version] = SurveyDefinition(
            surveyRound=3, surveyVersion=version, createdAt=timestamp,
            audience="elementary" if "-elem-" in version else "secondary",
            questions=[SurveyQuestion(key="demo.age", label="Age", csvColumn="demo.age", order=1)],
            spec={"part": 1 if "-part1-" in version else 2, "scales": []},
        )
    path = "/api/v1/researcher/exports/survey-responses/t3"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": f"Bearer {_participant_token()}"}).status_code == 403
    token = _token(client, "researcher", "researcher-password")
    response = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'attachment; filename="survey-t3-merged_' in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = {row["참여자 ID"]: row for row in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))}
    assert set(rows) == {"draft", "finished", "not-started", "secondary"}
    assert rows["finished"]["demo.age_p2"] == "15"
    assert rows["secondary"]["설문지 구분"] == "중고등용"
    assert rows["draft"]["파트 2 상태"] == "중간 저장"
    assert rows["draft"]["검사 1 저장 시각(KST)_p2"]
    assert rows["draft"]["demo.age_p2"] == ""
    assert rows["not-started"]["파트 2 상태"] == "시작 안 함"
    assert all(not rows["draft"][flag] for flag in FLAG_COLUMNS)
    assert all(value not in response.text for value in ("private-draft", "wrong-audience", "test-only"))


def test_t3_export_counts_only_displayed_answers_and_excludes_per_item_flag_from_overall():
    timestamp = datetime(2026, 11, 22, 12, tzinfo=UTC)
    questions = [{"key": f"question.{number}", "type": "text"} for number in range(50)]
    questions[0]["logic"] = {"type": "showIfEq", "dependsOn": "question.1", "equals": "trigger"}
    definition = SurveyDefinition(
        surveyRound=3, surveyVersion=T3_VERSIONS[0], createdAt=timestamp,
        questions=[SurveyQuestion(key=item["key"], csvColumn=item["key"], order=order)
                   for order, item in enumerate(questions)],
        spec={"scales": [{"scaleId": "sample", "questions": questions}]},
    )
    answers = _visible_answers(definition, dict.fromkeys((item["key"] for item in questions), "value"))
    assert "question.0" not in answers
    incomplete_metrics = ["", "", "", 100, 97, 3]
    complete_metrics = ["", "", "", 100, 97, 8]
    flags = _flags(definition, definition, answers, answers, complete_metrics, incomplete_metrics, 4)
    assert (flags[3], flags[4], flags[-1]) == (0, 1, 0)
    flags = _flags(definition, definition, answers, answers, incomplete_metrics, incomplete_metrics, 4)
    assert (flags[3], flags[4], flags[-1]) == (0, "", 0)


def test_partial_progress_export_is_researcher_only_and_excludes_final_responses() -> (
    None
):
    import asyncio
    import csv
    import io

    class Progress:
        async def list_incomplete(self, survey_round, survey_version):
            return [
                {
                    "participantId": participant_id,
                    "surveyRound": survey_round,
                    "surveyVersion": survey_version,
                    "revision": 3,
                    "completedPage": 2,
                    "nextPage": 3,
                    "totalPages": 8,
                    "answers": {"q1": "partial"},
                    "updatedAt": datetime.now(UTC).isoformat(),
                    "completed": False,
                    "detail": {
                        "demographicEntryPolicy": "reenter",
                        "demographicPolicyVersion": "2026-10-23",
                    },
                }
                for participant_id in ["participant-1", "partial-2"]
            ]

    with _client() as client:
        client.app.state.survey_progress_repository = Progress()
        asyncio.run(
            client.app.state.survey_definition_repository.create(
                SurveyDefinitionCreate(
                    surveyRound=2,
                    surveyVersion="t2-part1",
                    questions=[SurveyQuestion(key="q1", csvColumn="q1", order=1)],
                )
            )
        )
        path = (
            "/api/v1/researcher/survey-progress?survey_round=2&survey_version=t2-part1"
        )
        assert client.get(path).status_code == 401
        assert (
            client.get(
                path, headers={"Authorization": "Bearer " + _participant_token()}
            ).status_code
            == 403
        )
        headers = {
            "Authorization": "Bearer "
            + _token(client, "researcher", "researcher-password")
        }
        records = client.get(path, headers=headers).json()
        assert [record["participantId"] for record in records] == ["partial-2"]
        exported = client.get(path + "&format=csv", headers=headers)
        assert exported.status_code == 200
        rows = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
        assert len(rows) == 1
        assert rows[0]["recordStatus"] == "partial"
        assert rows[0]["completedPage"] == "2"
        assert rows[0]["demographicEntryPolicy"] == "reenter"


def _definition_payload() -> dict[str, object]:
    return {
        "surveyRound": 2,
        "surveyVersion": "2026-round-2-v2",
        "questions": [{"key": "q1", "csvColumn": "첫 번째 문항", "order": 1}],
    }


def test_survey_export_filename_uses_round_audience_part_and_kst() -> None:
    definition = SurveyDefinition(
        surveyRound=1,
        surveyVersion="t1-elem-part1-v2-0916",
        audience="elementary",
        questions=[SurveyQuestion(key="q1", csvColumn="첫 번째 문항", order=1)],
        spec={"part": 1},
        createdAt=datetime.now(UTC),
    )

    filename = _survey_export_filename(
        definition, datetime(2026, 9, 18, 15, 30, tzinfo=UTC)
    )

    assert filename == "T1_elem_part1_2026-09-19_00-30-00.csv"


def test_participant_can_restore_survey_progress_from_server() -> None:
    with _client() as client:
        response = client.get(
            "/api/v1/participant/survey-progress?survey_round=1",
            headers={"Authorization": f"Bearer {_participant_token()}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "surveyRound": 1,
        "completedVersions": ["t1-elem-part1-v2-0916"],
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
            headers={
                "Authorization": f"Bearer {researcher_token}",
                "Origin": "https://frontend.example.test",
            },
            params={"survey_round": 2, "survey_version": "2026-round-2-v2"},
        )

    assert create_response.status_code == 201
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith("text/csv")
    assert export_response.headers["content-disposition"].startswith(
        'attachment; filename="T2_all_part_'
    )
    assert (
        export_response.headers["access-control-expose-headers"]
        == "Content-Disposition"
    )
    assert (
        "아이디(휴대폰),학교급,학년,surveyRound,surveyVersion,응답 일시(KST),설문 시작 일시(KST),전체 경과시간(초),활동시간(초),재개 횟수,마지막 방문 페이지,방문한 페이지 수,전체 페이지 수,페이지 이동 횟수,첫 번째 문항"
        in export_response.text
    )
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
        client.post(
            "/api/v1/admin/survey-definitions",
            headers=headers,
            json=_definition_payload(),
        )

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
    assert [question["key"] for question in response.json()["questions"]] == [
        "q1",
        "q2",
    ]


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
                                    {
                                        "key": "demo.contact_phone",
                                        "label": "휴대폰 번호",
                                        "type": "tel",
                                    },
                                    {
                                        "key": "demo.contact_email",
                                        "label": "이메일",
                                        "type": "text",
                                    },
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
        {
            "key": "demo.contact",
            "csvColumn": "AI 활용동기 | 2 | 연구 참여자 연락처",
            "order": 2,
        },
        {
            "key": "demo.contact_phone",
            "csvColumn": "AI 활용동기 | 2 | 연구 참여자 연락처 | 휴대폰 번호",
            "order": 3,
        },
        {
            "key": "demo.contact_email",
            "csvColumn": "AI 활용동기 | 2 | 연구 참여자 연락처 | 이메일",
            "order": 4,
        },
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
                "_meta": {
                    "title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)"
                },
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
