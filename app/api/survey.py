from __future__ import annotations

from app.api.auth import _participant_id
from app.schemas.survey import SurveySubmissionAccepted, SurveySubmissionCreate
from app.services.jobs import JobRepository
from app.services.submissions import SurveySubmissionService, UnknownQuestionKeyError, UnknownSurveyDefinitionError
from app.services.surveys import SurveyDefinitionRepository
from fastapi import APIRouter, Depends, HTTPException, Request, status

router = APIRouter(prefix="/api/v1", tags=["survey submissions"])


def _job_repository(request: Request) -> JobRepository:
    repository = getattr(request.app.state, "job_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="제출 서비스를 준비 중입니다.")
    return repository


def _submission_service(request: Request) -> SurveySubmissionService:
    definitions: SurveyDefinitionRepository | None = getattr(request.app.state, "survey_definition_repository", None)
    if definitions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="설문 정의 서비스를 준비 중입니다."
        )
    return SurveySubmissionService(definitions, _job_repository(request))


@router.post("/survey-responses", response_model=SurveySubmissionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def submit_survey_response(
    submission: SurveySubmissionCreate,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> SurveySubmissionAccepted:
    try:
        job = await _submission_service(request).submit(participant_id, submission)
    except UnknownSurveyDefinitionError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="설문 정의를 찾을 수 없습니다.") from error
    except UnknownQuestionKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"알 수 없는 문항 키: {error}"
        ) from error
    return SurveySubmissionAccepted(submissionId=job.id, status=job.status)


@router.get("/submission-jobs/{submission_id}", response_model=SurveySubmissionAccepted)
async def get_submission_status(
    submission_id: str,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> SurveySubmissionAccepted:
    job = await _job_repository(request).get(submission_id)
    if (
        job is None
        or job.job_type not in {"survey_response", "chat_submission"}
        or job.payload.get("participantId") != participant_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="제출 작업을 찾을 수 없습니다.")
    return SurveySubmissionAccepted(submissionId=job.id, status=job.status)
