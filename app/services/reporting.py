from __future__ import annotations

from app.schemas.reporting import (
    NonparticipantRecord,
    NonparticipantReport,
    ParticipationStatus,
    SchoolLevelCount,
    SurveyRoundCount,
)
from app.services.auth import ParticipantAccountRepository
from app.services.surveys import SurveyResponseRepository


def _school_level_counts(participants: list[object]) -> SchoolLevelCount:
    levels = [getattr(participant, "school_level", None) for participant in participants]
    elementary = levels.count("초등")
    middle = levels.count("중등")
    high = levels.count("고등")
    return SchoolLevelCount(elementary=elementary, middle=middle, high=high, total=len(participants))


class ResearcherReportingService:
    def __init__(self, participants: ParticipantAccountRepository, responses: SurveyResponseRepository) -> None:
        self._participants = participants
        self._responses = responses

    async def participation_status(self) -> ParticipationStatus:
        participants = await self._participants.list_participants()
        responses = await self._responses.list_all_responses()
        rounds: dict[int, set[str]] = {}
        for response in responses:
            rounds.setdefault(response.survey_round, set()).add(response.participant_id)
        return ParticipationStatus(
            participants=_school_level_counts(participants),
            completedByRound=[
                SurveyRoundCount(surveyRound=survey_round, completedCount=len(participant_ids))
                for survey_round, participant_ids in sorted(rounds.items())
            ],
        )

    async def nonparticipants(self, survey_round: int, survey_version: str) -> NonparticipantReport:
        participants = await self._participants.list_participants()
        responses = await self._responses.list_responses(survey_round, survey_version)
        completed_ids = {response.participant_id for response in responses}
        missing = [participant for participant in participants if participant.participant_id not in completed_ids]
        return NonparticipantReport(
            surveyRound=survey_round,
            surveyVersion=survey_version,
            counts=_school_level_counts(missing),
            participants=[
                NonparticipantRecord(
                    participantId=participant.participant_id,
                    name=participant.name,
                    schoolLevel=participant.school_level,
                    grade=participant.grade,
                    phone=participant.phone,
                )
                for participant in missing
            ],
        )
