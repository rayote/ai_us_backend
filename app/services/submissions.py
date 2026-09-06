from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.jobs import Job, JobCreate
from app.schemas.survey import SurveyResponseRecord, SurveySubmissionCreate
from app.services.jobs import JobRepository
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository


class UnknownSurveyDefinitionError(Exception):
    """Raised when a submission refers to an unregistered survey version."""


class UnknownQuestionKeyError(Exception):
    """Raised when submitted answers do not match the registered definition."""


class SurveySubmissionService:
    def __init__(self, definitions: SurveyDefinitionRepository, jobs: JobRepository) -> None:
        self._definitions = definitions
        self._jobs = jobs

    async def submit(self, participant_id: str, submission: SurveySubmissionCreate) -> Job:
        definition = await self._definitions.get(submission.survey_round, submission.survey_version)
        if definition is None:
            raise UnknownSurveyDefinitionError
        known_keys = {question.key for question in definition.questions}
        if unknown_keys := set(submission.answers) - known_keys:
            raise UnknownQuestionKeyError(", ".join(sorted(unknown_keys)))
        return await self._jobs.enqueue(
            JobCreate(
                job_type="survey_response",
                idempotency_key=f"{participant_id}:{submission.submission_id}",
                payload={
                    "participantId": participant_id,
                    "surveyRound": submission.survey_round,
                    "surveyVersion": submission.survey_version,
                    "answers": submission.answers,
                },
            )
        )


async def store_survey_response(
    payload: dict[str, object],
    definitions: SurveyDefinitionRepository,
    responses: SurveyResponseRepository,
) -> None:
    survey_round = payload["surveyRound"]
    survey_version = payload["surveyVersion"]
    if not isinstance(survey_round, int) or not isinstance(survey_version, str):
        raise ValueError("Invalid survey submission job payload")
    if await definitions.get(survey_round, survey_version) is None:
        raise UnknownSurveyDefinitionError
    await responses.create_response(
        SurveyResponseRecord(
            participantId=str(payload["participantId"]),
            surveyRound=survey_round,
            surveyVersion=survey_version,
            answers=dict(payload["answers"]),
            submittedAt=datetime.now(UTC),
        )
    )
