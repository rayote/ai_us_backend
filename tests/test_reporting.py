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
