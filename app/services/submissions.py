from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.jobs import Job, JobCreate
from app.schemas.survey import SurveyResponseRecord, SurveySubmissionCreate
from app.services.auth import ParticipantAccount
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

    async def submit(
        self,
        participant_id: str,
        submission: SurveySubmissionCreate,
        participant: ParticipantAccount | None = None,
    ) -> Job:
        definition = await self._definitions.get(submission.survey_round, submission.survey_version)
        if definition is None:
            raise UnknownSurveyDefinitionError
        known_keys = {question.key for question in definition.questions}
        answers = _merge_participant_profile_answers(submission.answers, known_keys, participant)
        if unknown_keys := set(answers) - known_keys:
            raise UnknownQuestionKeyError(", ".join(sorted(unknown_keys)))
        return await self._jobs.enqueue(
            JobCreate(
                job_type="survey_response",
                idempotency_key=f"{participant_id}:{submission.submission_id}",
                payload={
                    "participantId": participant_id,
                    "surveyRound": submission.survey_round,
                    "surveyVersion": submission.survey_version,
                    "answers": answers,
                    "detail": submission.detail,
                },
            )
        )


def _merge_participant_profile_answers(
    answers: dict[str, object],
    known_keys: set[str],
    participant: ParticipantAccount | None,
) -> dict[str, object]:
    merged = dict(answers)
    if participant is None:
        return merged
    grade_answer = _grade_answer(participant.school_level, participant.grade)
    if grade_answer is not None and "demo.grade" in known_keys:
        merged["demo.grade"] = grade_answer
    if participant.phone and "demo.contact" in known_keys:
        contact = merged.get("demo.contact") if isinstance(merged.get("demo.contact"), dict) else {}
        merged["demo.contact"] = {**contact, "demo.contact_phone": participant.phone}
    if participant.guardian_phone and "demo.guardianContact" in known_keys:
        guardian = merged.get("demo.guardianContact") if isinstance(merged.get("demo.guardianContact"), dict) else {}
        merged["demo.guardianContact"] = {
            **guardian,
            "demo.guardianContact_phone": participant.guardian_phone,
        }
    return merged


def _grade_answer(school_level: str | None, grade: int | None) -> int | None:
    if school_level == "초등" and grade in {4, 5, 6}:
        return grade - 3
    if school_level == "중등" and grade in {1, 2, 3}:
        return grade + 3
    if school_level == "고등" and grade in {1, 2, 3}:
        return grade + 6
    return None


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
            detail=payload.get("detail") if isinstance(payload.get("detail"), dict) else None,
        )
    )
