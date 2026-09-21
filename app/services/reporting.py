from __future__ import annotations

from app.schemas.reporting import (
    ChatParticipationRecord,
    ChatParticipationStatus,
    NonparticipantRecord,
    NonparticipantReport,
    ParticipationStatus,
    SchoolLevelCount,
    SurveyRoundCount,
)
from app.services.auth import ParticipantAccountRepository
from app.services.chats import ChatSubmissionRepository
from app.services.surveys import SurveyResponseRepository


def _school_level_counts(participants: list[object]) -> SchoolLevelCount:
    levels = [getattr(participant, "school_level", None) for participant in participants]
    elementary = levels.count("초등")
    middle = levels.count("중등")
    high = levels.count("고등")
    return SchoolLevelCount(elementary=elementary, middle=middle, high=high, total=len(participants))


class ResearcherReportingService:
    def __init__(
        self,
        participants: ParticipantAccountRepository,
        responses: SurveyResponseRepository,
        chat_submissions: ChatSubmissionRepository | None = None,
    ) -> None:
        self._participants = participants
        self._responses = responses
        self._chat_submissions = chat_submissions

    async def participation_status(self) -> ParticipationStatus:
        participants = await self._participants.list_participants()
        responses = await self._responses.list_all_responses()
        participants_by_id = {participant.participant_id: participant for participant in participants}
        rounds: dict[int, set[str]] = {}
        for response in responses:
            rounds.setdefault(response.survey_round, set()).add(response.participant_id)

        chat_submissions = []
        if self._chat_submissions is not None:
            chat_submissions = await self._chat_submissions.list_submissions()
            chat_submissions.extend(await self._chat_submissions.list_submissions(status="deletion_requested"))
        chat_statuses: dict[str, dict[str, str]] = {}
        for submission in chat_submissions:
            statuses = chat_statuses.setdefault(submission.participant_id, {})
            if statuses.get(submission.submission_point) != "active":
                statuses[submission.submission_point] = submission.status
        consented = [participant for participant in participants if participant.chat_consent]
        submitted_after_round_1 = [
            participant for participant in participants if "afterRound1" in chat_statuses.get(participant.participant_id, {})
        ]
        submitted_after_round_4 = [
            participant for participant in participants if "afterRound4" in chat_statuses.get(participant.participant_id, {})
        ]
        chat_participant_ids = {participant.participant_id for participant in consented}
        chat_participant_ids.update(chat_statuses)
        return ParticipationStatus(
            participants=_school_level_counts(participants),
            completedByRound=[
                SurveyRoundCount(
                    surveyRound=survey_round,
                    completedCount=len(participant_ids),
                    counts=_school_level_counts(
                        [
                            participants_by_id[participant_id]
                            for participant_id in participant_ids
                            if participant_id in participants_by_id
                        ]
                    ),
                )
                for survey_round, participant_ids in sorted(rounds.items())
            ],
            chat=ChatParticipationStatus(
                consented=_school_level_counts(consented),
                submittedAfterRound1=_school_level_counts(submitted_after_round_1),
                submittedAfterRound4=_school_level_counts(submitted_after_round_4),
                participants=[
                    ChatParticipationRecord(
                        participantId=participant.participant_id,
                        name=participant.name,
                        schoolLevel=participant.school_level,
                        grade=participant.grade,
                        phone=participant.phone,
                        chatConsent=participant.chat_consent,
                        afterRound1=chat_statuses.get(participant.participant_id, {}).get(
                            "afterRound1", "not_submitted"
                        ),
                        afterRound4=chat_statuses.get(participant.participant_id, {}).get(
                            "afterRound4", "not_submitted"
                        ),
                    )
                    for participant in participants
                    if participant.participant_id in chat_participant_ids
                ],
            ),
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
