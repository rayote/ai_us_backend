from __future__ import annotations

from typing import Literal

from app.api.auth import require_researcher
from app.schemas.application import ApplicationApproval, ApplicationApprovalCompleted, ApplicationRecord
from app.services.applications import ApplicationRepository
from app.services.approvals import ApplicationApprovalService
from app.services.auth import ParticipantAccountRepository
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository, survey_responses_to_csv
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response

router = APIRouter(prefix="/api/v1/researcher", tags=["researcher"])


def _application_repository(request: Request) -> ApplicationRepository:
    repository = getattr(request.app.state, "application_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="신청 관리 서비스를 준비 중입니다."
        )
    return repository


def _approval_service(request: Request) -> ApplicationApprovalService:
    participant_repository: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participant_repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="승인 서비스를 준비 중입니다.")
    return ApplicationApprovalService(_application_repository(request), participant_repository)


def _survey_definition_repository(request: Request) -> SurveyDefinitionRepository:
    repository = getattr(request.app.state, "survey_definition_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="설문 정의 서비스를 준비 중입니다.")
    return repository


def _survey_response_repository(request: Request) -> SurveyResponseRepository:
    repository = getattr(request.app.state, "survey_response_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="설문 결과 서비스를 준비 중입니다.")
    return repository


@router.get("/applications", response_model=list[ApplicationRecord])
async def list_applications(
    school_level: Literal["elementary", "middle", "high"] | None = None,
    _: str = Depends(require_researcher),
    request: Request = None,
) -> list[ApplicationRecord]:
    return await _application_repository(request).list_applications(school_level)


@router.post("/applications/approve", response_model=ApplicationApprovalCompleted)
async def approve_applications(
    approval: ApplicationApproval,
    request: Request,
    _: str = Depends(require_researcher),
) -> ApplicationApprovalCompleted:
    approved_count = await _approval_service(request).approve(approval.application_ids)
    return ApplicationApprovalCompleted(approvedCount=approved_count)


@router.get("/exports/survey-responses")
async def export_survey_responses(
    survey_round: int,
    survey_version: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    definition = await _survey_definition_repository(request).get(survey_round, survey_version)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="설문 정의를 찾을 수 없습니다.")
    responses = await _survey_response_repository(request).list_responses(survey_round, survey_version)
    filename = f"survey-responses-round-{survey_round}-{survey_version}.csv"
    return Response(
        content="\ufeff" + survey_responses_to_csv(definition, responses),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
