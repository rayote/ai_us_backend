from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.core.settings import Settings
from app.main import create_app
from app.schemas.application import ApplicationCreate, ApplicationRecord
from app.services.application_settings import (
    ApplicationSettingsRepository,
    MongoApplicationSettingsRepository,
    default_survey_rounds,
)
from app.services.applications import (
    ApplicationRepository,
    DuplicateApplicationError,
    object_id,
)
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from fastapi.testclient import TestClient


class InMemoryApplicationRepository(ApplicationRepository):
    def __init__(self) -> None:
        self._phone_numbers: set[str] = set()
        self.records: dict[str, ApplicationRecord] = {}

    async def create(self, application: ApplicationCreate) -> str:
        if application.phone in self._phone_numbers:
            raise DuplicateApplicationError
        self._phone_numbers.add(application.phone)
        application_id = object_id()
        self.records[application_id] = ApplicationRecord(
            applicationId=application_id,
            gender=application.gender,
            grade=application.grade,
            phone=application.phone,
            guardianPhone=application.guardian_phone,
            email=application.email,
            consents=application.consents,
            status="pending",
            submittedAt=datetime.now(UTC),
        )
        return application_id

    async def list_applications(
        self, school_level: str | None = None
    ) -> list[ApplicationRecord]:
        return list(self.records.values())

    async def get_pending(self, application_ids: list[str]) -> list[ApplicationRecord]:
        return [
            self.records[application_id]
            for application_id in application_ids
            if application_id in self.records
            and self.records[application_id].status == "pending"
        ]

    async def approve(self, application_ids: list[str]) -> int:
        approved = 0
        for application_id in application_ids:
            record = self.records.get(application_id)
            if record is not None and record.status == "pending":
                self.records[application_id] = record.model_copy(
                    update={"status": "approved", "approved_at": datetime.now(UTC)}
                )
                approved += 1
        return approved


class InMemoryApplicationSettings(ApplicationSettingsRepository):
    def __init__(self, auto_approval: bool = False) -> None:
        self.auto_approval = auto_approval
        self.round_settings = {}

    async def auto_approval_enabled(self) -> bool:
        return self.auto_approval

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool:
        self.auto_approval = enabled
        return enabled

    async def survey_rounds(self, t1_open: bool) -> dict:
        return {**default_survey_rounds(t1_open), **self.round_settings}

    async def set_survey_round_open(
        self, survey_round: int, enabled: bool, updated_by: str
    ) -> None:
        self.round_settings[str(survey_round)] = {
            "is_open": enabled,
            "updated_at": datetime.now(UTC),
            "updated_by": updated_by,
        }


class RoundSettingsCollection:
    def __init__(self):
        self.document = {"auto_approval": True}
        self.updates = []

    async def find_one(self, query):
        return self.document

    async def update_one(self, query, update, upsert):
        assert query == {"_id": "participant_application"} and upsert is True
        self.updates.append(update)
        for path, value in update["$set"].items():
            target = self.document
            keys = path.split(".")
            for key in keys[:-1]:
                target = target.setdefault(key, {})
            target[keys[-1]] = value


class RoundDefinitions:
    async def get(self, survey_round, survey_version):
        from app.schemas.survey import SurveyDefinition, SurveyQuestion

        summary = next(
            (
                item
                for item in await self.list_definitions()
                if item.survey_round == survey_round
                and item.survey_version == survey_version
            ),
            None,
        )
        if summary is None:
            return None
        return SurveyDefinition(
            surveyRound=survey_round,
            surveyVersion=survey_version,
            audience=summary.audience,
            questions=[SurveyQuestion(key="q1", csvColumn="q1", order=1)],
            spec={
                "part": summary.part,
                "scales": [
                    {"scaleId": "test", "questions": [{"key": "q1", "type": "text"}]}
                ],
            },
            createdAt=datetime.now(UTC),
        )

    async def list_definitions(self):
        from app.schemas.survey import SurveyDefinitionSummary

        return [
            SurveyDefinitionSummary(
                surveyRound=round_number,
                surveyVersion=f"t{round_number}-{audience + '-' if audience != 'all' else ''}part{part}-{version}",
                audience=audience,
                part=part,
                questionCount=1,
                createdAt=datetime.now(UTC),
            )
            for round_number, audiences, version in [
                (1, ["elementary", "secondary"], "v2-0916"),
                (2, ["all"], "v9-1022"),
            ]
            for audience in audiences
            for part in [1, 2]
        ]


def test_survey_round_settings_defaults_permissions_upsert_and_validation():
    secret = "test-secret-at-least-thirty-two-bytes"
    collection = RoundSettingsCollection()
    repository = MongoApplicationSettingsRepository(collection)
    app = create_app(
        Settings("test", None, "ai_us_test", (), secret, t1_submissions_open=False),
        application_settings_repository=repository,
        survey_definition_repository=RoundDefinitions(),
    )

    def headers(role):
        return {
            "Authorization": "Bearer "
            + jwt.encode(
                {
                    "sub": role + "-1",
                    "role": role,
                    "exp": datetime.now(UTC) + timedelta(minutes=10),
                },
                secret,
                algorithm="HS256",
            )
        }

    with TestClient(app) as client:
        path = "/api/v1/researcher/survey-round-settings"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers("participant")).status_code == 403
        result = client.get(path, headers=headers("researcher"))
        assert result.status_code == 200
        assert [record["isOpen"] for record in result.json()] == [
            False,
            True,
            False,
            False,
        ]
        assert collection.updates == []
        update_path = "/api/v1/admin/survey-round-settings/2"
        assert (
            client.put(
                update_path, json={"isOpen": False}, headers=headers("researcher")
            ).status_code
            == 403
        )
        assert (
            client.put(
                update_path, json={"isOpen": False}, headers=headers("participant")
            ).status_code
            == 403
        )
        assert client.put(update_path, json={"isOpen": False}).status_code == 401
        for invalid in [
            {"isOpen": "false"},
            {"isOpen": 0},
            {"isOpen": None},
            {"isOpen": False, "autoApproval": False},
            {},
        ]:
            assert (
                client.put(
                    update_path, json=invalid, headers=headers("admin")
                ).status_code
                == 422
            )
        for invalid_round in [0, 5]:
            assert (
                client.put(
                    f"/api/v1/admin/survey-round-settings/{invalid_round}",
                    json={"isOpen": False},
                    headers=headers("admin"),
                ).status_code
                == 422
            )
        result = client.put(
            update_path, json={"isOpen": False}, headers=headers("admin")
        )
        assert result.status_code == 200
        assert result.json()["isOpen"] is False
        assert result.json()["updatedBy"] == "admin-1"
        assert result.json()["updatedAt"]
        assert collection.document["auto_approval"] is True
        assert (
            client.put(
                "/api/v1/admin/survey-round-settings/1",
                json={"periodEnded": False},
                headers=headers("admin"),
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/v1/admin/survey-round-settings/1",
                json={"isOpen": True},
                headers=headers("admin"),
            ).status_code
            == 200
        )
        assert (
            client.put(
                "/api/v1/admin/application-settings",
                json={"autoApproval": False},
                headers=headers("admin"),
            ).status_code
            == 200
        )
        result = client.get(path, headers=headers("admin")).json()
        assert [record["isOpen"] for record in result] == [True, False, False, False]
        assert all(
            set(update["$set"]).issubset({"auto_approval", "updated_at", "updated_by"})
            or all(key.startswith("survey_rounds.") for key in update["$set"])
            for update in collection.updates
        )


def test_round_configuration_and_period_end_are_reversible_and_independent():
    secret = "test-secret-at-least-thirty-two-bytes"
    collection = RoundSettingsCollection()
    app = create_app(
        Settings("test", None, "ai_us_test", (), secret, t1_submissions_open=False),
        application_settings_repository=MongoApplicationSettingsRepository(collection),
        survey_definition_repository=RoundDefinitions(),
    )
    admin = {
        "Authorization": "Bearer "
        + jwt.encode(
            {
                "sub": "admin",
                "role": "admin",
                "exp": datetime.now(UTC) + timedelta(minutes=10),
            },
            secret,
            algorithm="HS256",
        )
    }
    path = "/api/v1/admin/survey-round-settings/2"
    with TestClient(app) as client:
        initial = client.get(
            "/api/v1/researcher/survey-round-settings", headers=admin
        ).json()
        assert [item["state"] for item in initial] == [
            "closed",
            "open",
            "preparing",
            "preparing",
        ]
        assert (
            initial[2]["configurations"] == [] and initial[3]["configurationId"] is None
        )
        for changes in [
            {"isOpen": True},
            {"configurationId": None},
            {"periodEnded": True},
        ]:
            assert (
                client.put(
                    "/api/v1/admin/survey-round-settings/3", headers=admin, json=changes
                ).status_code
                == 409
            )
        for changes in [
            {"periodEnded": "true"},
            {"periodEnded": None},
            {"isOpen": False, "periodEnded": False},
            {"configurationId": ""},
        ]:
            assert client.put(path, headers=admin, json=changes).status_code == 422
        assert (
            client.put(
                path, headers=admin, json={"configurationId": "unknown"}
            ).status_code
            == 422
        )
        preparing = client.put(
            path, headers=admin, json={"configurationId": None}
        ).json()
        assert preparing["state"] == "preparing" and preparing["isOpen"] is False
        assert preparing["submissionAllowed"] is True
        assert client.put(path, headers=admin, json={"isOpen": True}).status_code == 409
        reopened = client.put(
            path, headers=admin, json={"configurationId": "t2-parts-v9-1022"}
        ).json()
        assert reopened["state"] == "open"
        closed = client.put(path, headers=admin, json={"periodEnded": True}).json()
        assert closed["state"] == "closed" and closed["submissionAllowed"] is False
        assert client.put(path, headers=admin, json={"isOpen": True}).status_code == 409
        unended = client.put(path, headers=admin, json={"periodEnded": False}).json()
        assert unended["state"] == "preparing" and unended["submissionAllowed"] is False
        assert (
            client.put(path, headers=admin, json={"isOpen": True}).json()["state"]
            == "open"
        )
        paused = client.put(path, headers=admin, json={"isOpen": False}).json()
        assert paused["state"] == "preparing" and paused["periodEnded"] is False
        assert collection.document["auto_approval"] is True
        assert "1" not in collection.document["survey_rounds"]


def test_survey_configuration_requires_complete_unambiguous_release():
    import asyncio

    from app.services.application_settings import survey_configurations

    records = asyncio.run(RoundDefinitions().list_definitions())
    assert [item["id"] for item in survey_configurations(records)["1"]] == [
        "t1-parts-v2-0916"
    ]
    t2 = [item for item in records if item.survey_round == 2]
    assert survey_configurations(t2[:1])["2"] == []
    assert survey_configurations(t2 + [t2[0]])["2"] == []
    assert (
        survey_configurations(
            [item for item in records if item.audience != "secondary"]
        )["1"]
        == []
    )
    future = [item.model_copy(update={"survey_round": 3}) for item in t2]
    assert len(survey_configurations(future)["3"]) == 1


class InMemoryParticipantRepository(ParticipantAccountRepository):
    def __init__(self, existing_phone: str | None = None) -> None:
        self.accounts: dict[str, ParticipantAccount] = {}
        if existing_phone is not None:
            self.accounts[existing_phone] = ParticipantAccount(
                "participant-1", existing_phone, "hash", False, False, "초등"
            )

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        return self.accounts.get(phone)

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def reset_password_by_phone_email(
        self, phone: str, email: str, password_hash: str
    ) -> bool:
        return False

    async def create(
        self,
        phone: str,
        password_hash: str,
        chat_consent: bool = False,
        school_level: str | None = None,
        email: str | None = None,
        guardian_phone: str | None = None,
        sns: str | None = None,
        grade: int | None = None,
    ) -> bool:
        if phone in self.accounts:
            return False
        self.accounts[phone] = ParticipantAccount(
            f"participant-{len(self.accounts) + 1}",
            phone,
            password_hash,
            True,
            chat_consent,
            school_level,
            email=email,
            guardian_phone=guardian_phone,
            sns=sns,
            grade=grade,
        )
        return True

    async def create_imported(
        self,
        phone: str,
        password_hash: str,
        name: str,
        school_level: str,
        grade: int,
        email: str | None = None,
    ) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return []


def _client(
    existing_participant_phone: str | None = None, auto_approval: bool = False
) -> TestClient:
    settings = Settings(
        "test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes"
    )
    return TestClient(
        create_app(
            settings,
            application_repository=InMemoryApplicationRepository(),
            application_settings_repository=InMemoryApplicationSettings(auto_approval),
            participant_account_repository=InMemoryParticipantRepository(
                existing_participant_phone
            ),
        )
    )


def _application_payload() -> dict[str, object]:
    return {
        "gender": "여",
        "grade": "중학교 2학년",
        "phone": "010-1234-5678",
        "guardianPhone": "010-9876-5432",
        "email": "participant@example.com",
        "consents": {
            "documentRead": True,
            "survey": True,
            "chat": False,
            "participant": True,
            "guardian": True,
        },
    }


def test_create_application_returns_pending_status() -> None:
    with _client() as client:
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert len(response.json()["applicationId"]) == 24


def test_create_application_auto_approves_and_returns_password_change_login() -> None:
    with _client(auto_approval=True) as client:
        response = client.post("/api/v1/applications", json=_application_payload())
        account = client.app.state.participant_account_repository.accounts[
            "01012345678"
        ]

    assert response.status_code == 201
    assert response.json()["status"] == "approved"
    assert response.json()["accessToken"]
    assert response.json()["needsPasswordChange"] is True
    assert response.json()["audience"] == "secondary"
    assert response.json()["schoolLevel"] == "중등"
    assert response.json()["grade"] == 2
    assert response.json()["guardianPhone"] == "01098765432"
    assert account.guardian_phone == "01098765432"
    assert account.grade == 2
    claims = jwt.decode(
        response.json()["accessToken"],
        "test-secret-at-least-thirty-two-bytes",
        algorithms=["HS256"],
    )
    assert 239 * 60 <= claims["exp"] - datetime.now(UTC).timestamp() <= 240 * 60


def test_create_application_rejects_duplicate_phone_number() -> None:
    with _client() as client:
        client.post("/api/v1/applications", json=_application_payload())
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "이미 신청된 휴대폰 번호입니다. 연구자 승인 후 로그인해 주세요."
    )


def test_create_application_rejects_same_participant_and_guardian_phone() -> None:
    payload = _application_payload()
    payload["guardianPhone"] = payload["phone"]

    with _client() as client:
        response = client.post("/api/v1/applications", json=payload)

    assert response.status_code == 422


def test_create_application_rejects_registered_participant_phone_number() -> None:
    with _client("01012345678") as client:
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "이미 등록된 참여자 휴대폰 번호입니다. 로그인하거나 비밀번호 찾기를 이용해 주세요."
    )


@pytest.mark.parametrize("auto_approval", [False, True])
def test_create_application_rejects_registered_participant_as_guardian(
    auto_approval: bool,
) -> None:
    with _client("01098765432", auto_approval=auto_approval) as client:
        response = client.post("/api/v1/applications", json=_application_payload())
        assert client.app.state.application_repository.records == {}
        assert len(client.app.state.participant_account_repository.accounts) == 1

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "이미 가입된 사용자 번호는 보호자 번호로 입력할 수 없어요. 연락처를 다시 확인하세요."
    )
    assert "accessToken" not in response.json()


def test_create_application_allows_siblings_sharing_guardian() -> None:
    with _client(auto_approval=True) as client:
        first = client.post("/api/v1/applications", json=_application_payload())
        payload = _application_payload()
        payload["phone"] = "010-1234-5679"
        second = client.post("/api/v1/applications", json=payload)

    assert first.status_code == second.status_code == 201
    assert (
        first.json()["guardianPhone"] == second.json()["guardianPhone"] == "01098765432"
    )


def test_create_application_allows_matching_phone_suffix() -> None:
    with _client("01011115432") as client:
        response = client.post("/api/v1/applications", json=_application_payload())

    assert response.status_code == 201


def test_create_application_requires_all_required_consents() -> None:
    payload = _application_payload()
    payload["consents"]["guardian"] = False  # type: ignore[index]

    with _client() as client:
        response = client.post("/api/v1/applications", json=payload)

    assert response.status_code == 422
