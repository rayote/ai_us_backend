import asyncio
from datetime import UTC, datetime

from app.schemas.survey import SurveyResponseRecord
from app.services.auth import ParticipantAccount, ParticipantAccountRepository
from app.services.reporting import ResearcherReportingService
from app.services.surveys import SurveyResponseRepository


class InMemoryParticipants(ParticipantAccountRepository):
    def __init__(self) -> None:
        self.participants = [
            ParticipantAccount("1", "01011111111", "hash", False, school_level="초등", name="가", grade=4),
            ParticipantAccount("2", "01022222222", "hash", False, school_level="중등", name="나", grade=2),
            ParticipantAccount("3", "01033333333", "hash", False, school_level="고등", name="다", grade=1),
        ]

    async def find_by_phone(self, phone: str):
        return None

    async def find_by_id(self, participant_id: str):
        return None

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        return False

    async def create(self, phone: str, password_hash: str, chat_consent: bool = False, school_level: str | None = None) -> bool:
        return False

    async def create_imported(self, phone: str, password_hash: str, name: str, school_level: str, grade: int) -> bool:
        return False

    async def list_participants(self) -> list[ParticipantAccount]:
        return self.participants


class InMemoryResponses(SurveyResponseRepository):
    async def create_response(self, response: SurveyResponseRecord) -> None:
        return None

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]:
        return [
            SurveyResponseRecord(participantId="1", surveyRound=survey_round, surveyVersion=survey_version, answers={}, submittedAt=datetime.now(UTC))
        ]

    async def list_all_responses(self) -> list[SurveyResponseRecord]:
        return [
            SurveyResponseRecord(participantId="1", surveyRound=1, surveyVersion="v1", answers={}, submittedAt=datetime.now(UTC)),
            SurveyResponseRecord(participantId="2", surveyRound=1, surveyVersion="v1", answers={}, submittedAt=datetime.now(UTC)),
        ]


def test_reporting_counts_participants_and_nonparticipants() -> None:
    service = ResearcherReportingService(InMemoryParticipants(), InMemoryResponses())

    status = asyncio.run(service.participation_status())
    missing = asyncio.run(service.nonparticipants(1, "v1"))

    assert status.participants.model_dump() == {"elementary": 1, "middle": 1, "high": 1, "total": 3}
    assert status.completed_by_round[0].completed_count == 2
    assert missing.counts.total == 2
    assert [participant.name for participant in missing.participants] == ["나", "다"]