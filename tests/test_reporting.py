import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.db.mongodb import AsyncCollection
from app.schemas.survey import SurveyDefinitionSummary, SurveyResponseRecord
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from app.services.chats import ChatSubmissionSummary, MongoChatSubmissionRepository
from app.services.reporting import ResearcherReportingService
from app.services.surveys import (
    MongoSurveyResponseRepository,
    SurveyDefinitionRepository,
    SurveyResponseRepository,
    SurveyResponseSummary,
)


def test_allowlist_groups_only_include_authorized_members_and_keep_unassigned():
    from app.services.reporting import survey_allowlist_groups

    records = {"2": {"followup_participant_ids": ["one", "two", "one", "three"]},
               "3": {"followup_participant_ids": ["school-a", "school-b"], "followup_groups": [
                   {"id": "school-a", "label": "학교 A", "participant_ids": ["school-a", "outsider", "school-a"]},
                   {"id": "school-b", "label": "학교 B", "participant_ids": ["school-b"]},
               ]}}
    groups = survey_allowlist_groups(records)
    assert groups[0]["participant_ids"] == ["one", "two", "three"]
    assert [group["id"] for group in groups] == ["t2-ungrouped", "t3-school-a", "t3-school-b"]
    assert groups[1]["participant_ids"] == ["school-a"]
    assert survey_allowlist_groups({}) == []


def test_t1_school_groups_report_only_t1_progress_and_normalize_mongo_deadline():
    from unittest.mock import AsyncMock
    from app.services.reporting import survey_allowlist_groups

    deadline = datetime(2040, 1, 1)
    records = {"1": {"followup_participant_ids": ["one", "two"], "followup_deadline": deadline,
                     "followup_groups": [
                         {"id": "school-a", "label": "School A", "participant_ids": ["one", "outsider"]},
                         {"id": "school-b", "label": "School B", "participant_ids": ["two"]},
                     ]}}
    groups = survey_allowlist_groups(records)
    assert [group["id"] for group in groups] == ["t1-school-a", "t1-school-b"]
    assert groups[0]["participant_ids"] == ["one"]
    assert groups[0]["deadline"] == deadline.replace(tzinfo=UTC)
    service = ResearcherReportingService(
        SimpleNamespace(list_allowlist_accounts=AsyncMock(return_value=[{"_id": "one"}])),
        SimpleNamespace(list_allowlist_responses=AsyncMock(return_value=[SurveyResponseSummary("one", 1, "t1-part1")])),
        SimpleNamespace(list_definitions=AsyncMock(return_value=[
            SimpleNamespace(survey_round=1, survey_version="t1-part1", part=1),
            SimpleNamespace(survey_round=2, survey_version="t2-part2", part=2),
        ])),
    )
    result = asyncio.run(service.survey_allowlist(groups[0]))
    assert result["surveyRound"] == 1 and result["deadline"].tzinfo == UTC
    assert result["counts"]["partial"] == 1 and result["counts"]["completed"] == 0
    service._responses.list_allowlist_responses.assert_awaited_once_with(["one"], 1)


def test_allowlist_report_is_read_only_complete_and_role_protected():
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.core.settings import Settings
    from app.core.security import create_access_token

    first = datetime(2026, 11, 10, 1, tzinfo=UTC)
    second = datetime(2026, 11, 10, 2, tzinfo=UTC)

    class Accounts:
        async def list_allowlist_accounts(self, identities):
            assert identities == ["one", "two", "three", "missing"]
            return [{"_id": identifier, "name": identifier, "phone_normalized": "01000000000", "school_level": "초등", "grade": 5}
                    for identifier in identities if identifier != "missing"]

    class Responses:
        async def list_allowlist_responses(self, identities, survey_round):
            assert identities == ["one", "two", "three", "missing"] and survey_round == 2
            return [SurveyResponseSummary("one", 2, "part1", first), SurveyResponseSummary("one", 2, "part2", second.replace(tzinfo=None)),
                    SurveyResponseSummary("two", 2, "part1", first), SurveyResponseSummary("three", 2, "unknown", second)]

    class Definitions:
        async def list_definitions(self):
            return [SimpleNamespace(survey_round=2, survey_version=f"part{part}", part=part) for part in (1, 2)]

    class SettingsRepository:
        async def survey_rounds(self, t1_open):
            return {"2": {"followup_participant_ids": ["one", "two", "three", "missing"]}}

    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(Settings("test", None, "ai_us_test", (), secret), participant_account_repository=Accounts(),
        survey_response_repository=Responses(), survey_definition_repository=Definitions(), application_settings_repository=SettingsRepository())
    def headers(role):
        return {"Authorization": "Bearer " + create_access_token(role + "-test", role, secret, 60)}
    with TestClient(app) as client:
        path = "/api/v1/researcher/survey-allowlists"
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers("participant")).status_code == 403
        for role in ("admin", "researcher"):
            groups = client.get(path, headers=headers(role)).json()
            assert groups == [{"id": "t2-ungrouped", "label": "T2 후속 참여 대상자", "surveyRound": 2, "total": 4}]
            report = client.get(path + "/t2-ungrouped", headers=headers(role))
            assert report.status_code == 200
            result = report.json()
            assert result["counts"] == {"total": 4, "completed": 1, "partial": 1, "notSubmitted": 1, "accountMissing": 1}
            assert [row["status"] for row in result["participants"]] == ["completed", "partial", "notSubmitted", "accountMissing"]
            assert result["participants"][0]["submittedAt"] == "2026-11-10T02:00:00Z"
            assert result["participants"][2]["submittedAt"] is None
            assert result["deadline"] == "2026-11-15T15:00:00Z"
            assert client.get(path + "/missing", headers=headers(role)).status_code == 404
            assert client.post(path, headers=headers(role), json={}).status_code == 405


def test_allowlist_completion_preserves_legacy_missing_submission_time():
    from unittest.mock import AsyncMock

    service = ResearcherReportingService(
        SimpleNamespace(list_allowlist_accounts=AsyncMock(return_value=[{"_id": "one"}])),
        SimpleNamespace(list_allowlist_responses=AsyncMock(return_value=[SurveyResponseSummary("one", 2, "part2")])),
        SimpleNamespace(list_definitions=AsyncMock(return_value=[SimpleNamespace(survey_round=2, survey_version="part2", part=2)])),
    )
    result = asyncio.run(service.survey_allowlist({"id": "t2-test", "label": "test", "surveyRound": 2, "participant_ids": ["one"]}))
    assert result["counts"]["completed"] == 1
    assert result["participants"][0]["submittedAt"] is None


def test_allowlist_queries_are_member_scoped_metadata_projections():
    from bson import ObjectId
    from app.services.auth import MongoParticipantAccountRepository

    identifier = "507f1f77bcf86cd799439011"
    participants, responses = MagicMock(), MagicMock()
    participants.find.return_value = iter([{"_id": ObjectId(identifier), "name": "synthetic"}])
    responses.find.return_value = iter([{"participant_id": identifier, "survey_round": 2, "survey_version": "part2", "submitted_at": datetime.now(UTC)}])
    accounts = asyncio.run(MongoParticipantAccountRepository(AsyncCollection(participants)).list_allowlist_accounts([identifier]))
    summaries = asyncio.run(MongoSurveyResponseRepository(AsyncCollection(responses)).list_allowlist_responses([identifier], 2))
    assert len(accounts) == len(summaries) == 1
    participants.find.assert_called_once_with({"_id": {"$in": [ObjectId(identifier)]}, "role": "participant"},
        projection={"_id": 1, "name": 1, "phone_normalized": 1, "school_level": 1, "grade": 1})
    responses.find.assert_called_once_with({"participant_id": {"$in": [identifier]}, "survey_round": 2},
        projection={"_id": 0, "participant_id": 1, "survey_round": 1, "survey_version": 1, "submitted_at": 1})


class InMemoryParticipants(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.participants = [
            ParticipantAccount(
                "1",
                "01011111111",
                "hash",
                False,
                school_level="초등",
                name="가",
                grade=4,
            ),
            ParticipantAccount(
                "2",
                "01022222222",
                "hash",
                False,
                school_level="중등",
                name="나",
                grade=2,
            ),
            ParticipantAccount(
                "3",
                "01033333333",
                "hash",
                False,
                school_level="고등",
                name="다",
                grade=1,
            ),
        ]

    async def find_by_phone(self, phone: str):
        return None

    async def find_by_id(self, participant_id: str):
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
    ) -> bool:
        return False

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int
    ) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return self.participants


class InMemoryResponses(SurveyResponseRepository):
    async def list_response_summaries(self):
        return [
            SurveyResponseSummary(
                response.participant_id, response.survey_round, response.survey_version
            )
            for response in await self.list_all_responses()
        ]

    async def create_response(self, response: SurveyResponseRecord) -> None:
        return None

    async def list_responses(
        self, survey_round: int, survey_version: str
    ) -> list[SurveyResponseRecord]:
        return [
            SurveyResponseRecord(
                participantId="1",
                surveyRound=survey_round,
                surveyVersion=survey_version,
                answers={},
                submittedAt=datetime.now(UTC),
            )
        ]

    async def list_all_responses(self) -> list[SurveyResponseRecord]:
        return [
            SurveyResponseRecord(
                participantId="1",
                surveyRound=1,
                surveyVersion="part1",
                answers={},
                submittedAt=datetime.now(UTC),
            ),
            SurveyResponseRecord(
                participantId="2",
                surveyRound=1,
                surveyVersion="part1",
                answers={},
                submittedAt=datetime.now(UTC),
            ),
            SurveyResponseRecord(
                participantId="1",
                surveyRound=1,
                surveyVersion="part2",
                answers={},
                submittedAt=datetime.now(UTC),
            ),
            SurveyResponseRecord(
                participantId="3",
                surveyRound=2,
                surveyVersion="part2",
                answers={},
                submittedAt=datetime.now(UTC),
            ),
        ]


class InMemoryDefinitions(SurveyDefinitionRepository):
    async def list_definitions(self) -> list[SurveyDefinitionSummary]:
        created_at = datetime.now(UTC)
        return [
            SurveyDefinitionSummary(
                surveyRound=1,
                surveyVersion="part1",
                audience="elementary",
                part=1,
                questionCount=1,
                createdAt=created_at,
            ),
            SurveyDefinitionSummary(
                surveyRound=1,
                surveyVersion="part2",
                audience="elementary",
                part=2,
                questionCount=1,
                createdAt=created_at,
            ),
            SurveyDefinitionSummary(
                surveyRound=2,
                surveyVersion="part2",
                audience="secondary",
                part=2,
                questionCount=1,
                createdAt=created_at,
            ),
            SurveyDefinitionSummary(
                surveyRound=3,
                surveyVersion="part2",
                audience="secondary",
                part=2,
                questionCount=1,
                createdAt=created_at,
            ),
        ]


class InMemoryChatSubmissions:
    async def list_submission_summaries(self):
        return [
            ChatSubmissionSummary(
                submission.participant_id, submission.submission_point, submission.status
            )
            for status in ("active", "deletion_requested")
            for submission in await self.list_submissions(status=status)
        ]

    async def list_submissions(self, submission_point=None, status="active"):
        submissions = [
            SimpleNamespace(
                participant_id="1", submission_point="afterRound1", status="active"
            ),
            SimpleNamespace(
                participant_id="2",
                submission_point="afterRound4",
                status="deletion_requested",
            ),
        ]
        return [
            submission
            for submission in submissions
            if submission.status == status
            and (
                submission_point is None
                or submission.submission_point == submission_point
            )
        ]


def test_reporting_counts_participants_and_nonparticipants() -> None:
    participants = InMemoryParticipants()
    participants.participants[0] = ParticipantAccount(
        "1", "01011111111", "hash", False, True, "초등", "가", 4
    )
    service = ResearcherReportingService(
        participants,
        InMemoryResponses(),
        InMemoryDefinitions(),
        InMemoryChatSubmissions(),
    )

    status = asyncio.run(service.participation_status())
    missing = asyncio.run(service.nonparticipants(1, "v1"))

    assert status.participants.model_dump() == {
        "elementary": 1,
        "middle": 1,
        "high": 1,
        "total": 3,
    }
    assert status.completed_by_round[0].completed_count == 1
    assert status.completed_by_round[0].counts.model_dump() == {
        "elementary": 1,
        "middle": 0,
        "high": 0,
        "total": 1,
    }
    assert status.completed_by_round[1].counts.high == 1
    assert status.completed_by_round[2].survey_round == 3
    assert status.completed_by_round[2].completed_count == 0
    assert status.chat.consented.total == 1
    assert status.chat.submitted_after_round_1.elementary == 1
    assert status.chat.submitted_after_round_4.middle == 1
    assert "participants" not in status.chat.model_dump()
    assert missing.counts.total == 2
    assert [participant.name for participant in missing.participants] == ["나", "다"]

    survey_incomplete = asyncio.run(service.incomplete_participants("survey", "1"))
    assert [participant.name for participant in survey_incomplete.participants] == [
        "나",
        "다",
    ]

    chat_incomplete = asyncio.run(
        service.incomplete_participants("chat", "afterRound4")
    )
    assert [participant.name for participant in chat_incomplete.participants] == ["가"]

    elementary_chat_incomplete = asyncio.run(
        service.incomplete_participants("chat", "afterRound1", "초등")
    )
    assert elementary_chat_incomplete.counts.total == 0


def test_participation_status_uses_unsorted_metadata_only_queries() -> None:
    responses = MagicMock()
    responses.find.return_value = iter([
        {"participant_id": "1", "survey_round": 1, "survey_version": "part1"},
        {"participant_id": "1", "survey_round": 1, "survey_version": "part2"},
        {"participant_id": "1", "survey_round": 1, "survey_version": "part2"},
        {"participant_id": "missing", "survey_round": 1, "survey_version": "part2"},
        {"participant_id": "3", "survey_round": 2, "survey_version": "part2"},
    ])
    submissions = MagicMock()
    submissions.find.return_value = iter([
        {"participant_id": "1", "submission_point": "afterRound1"},
        {"participant_id": "1", "submission_point": "afterRound1", "status": "active"},
        {"participant_id": "2", "submission_point": "afterRound2", "status": "deletion_requested"},
        {"participant_id": "2", "submission_point": "afterRound3", "status": "active"},
        {"participant_id": "3", "submission_point": "afterRound4", "status": "active"},
        {"participant_id": "missing", "submission_point": "afterRound4", "status": "active"},
    ])
    service = ResearcherReportingService(
        InMemoryParticipants(),
        MongoSurveyResponseRepository(AsyncCollection(responses)),
        InMemoryDefinitions(),
        MongoChatSubmissionRepository(AsyncCollection(submissions)),
    )

    status = asyncio.run(service.participation_status())

    assert status.participants.total == 3
    assert status.completed_by_round[0].completed_count == 2
    assert status.completed_by_round[0].counts.elementary == 1
    assert status.completed_by_round[0].counts.total == 1
    assert status.completed_by_round[1].counts.high == 1
    assert status.completed_by_round[2].completed_count == 0
    assert status.chat.consented.total == 0
    assert status.chat.submitted_after_round_1.total == 1
    assert status.chat.submitted_after_round_2.middle == 1
    assert status.chat.submitted_after_round_3.middle == 1
    assert status.chat.submitted_after_round_4.high == 1
    assert status.chat.submitted_after_round_4.total == 1
    responses.find.assert_called_once_with(
        {},
        projection={"_id": 0, "participant_id": 1, "survey_round": 1, "survey_version": 1},
    )
    submissions.find.assert_called_once_with(
        {"$or": [
            {"status": {"$in": ["active", "deletion_requested"]}},
            {"status": {"$exists": False}},
        ]},
        projection={"_id": 0, "participant_id": 1, "submission_point": 1, "status": 1},
    )
