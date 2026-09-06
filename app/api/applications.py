from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.schemas.application import ApplicationCreate, ApplicationCreated
from app.services.applications import ApplicationRepository, DuplicateApplicationError

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def _repository(request: Request) -> ApplicationRepository:
    repository = getattr(request.app.state, "application_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="신청 저장 서비스를 준비 중입니다.",
        )
    return repository


@router.post("", response_model=ApplicationCreated, status_code=status.HTTP_201_CREATED)
async def create_application(application: ApplicationCreate, request: Request) -> ApplicationCreated:
    try:
        application_id = await _repository(request).create(application)
    except DuplicateApplicationError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 신청된 휴대폰 번호입니다.",
        ) from error

    return ApplicationCreated(applicationId=application_id, status="pending")
