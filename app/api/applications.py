from __future__ import annotations

from app.schemas.application import ApplicationCreate, ApplicationCreated
from app.services.auth import ParticipantAccountRepository
from app.services.applications import ApplicationRepository, DuplicateApplicationError
from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def _repository(request: Request) -> ApplicationRepository:
    repository = getattr(request.app.state, "application_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="신청 저장 서비스를 준비 중입니다.",
        )
    return repository


def _participant_repository(request: Request) -> ParticipantAccountRepository | None:
    return getattr(request.app.state, "participant_account_repository", None)


@router.post("", response_model=ApplicationCreated, status_code=status.HTTP_201_CREATED)
async def create_application(application: ApplicationCreate, request: Request) -> ApplicationCreated:
    participant_repository = _participant_repository(request)
    if participant_repository is not None and await participant_repository.find_by_phone(application.phone) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 등록된 참여자 휴대폰 번호입니다. 로그인하거나 비밀번호 찾기를 이용해 주세요.",
        )
    try:
        application_id = await _repository(request).create(application)
    except DuplicateApplicationError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 신청된 휴대폰 번호입니다. 연구자 승인 후 로그인해 주세요.",
        ) from error

    return ApplicationCreated(applicationId=application_id, status="pending")
