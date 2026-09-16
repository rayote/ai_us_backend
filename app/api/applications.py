from __future__ import annotations

from app.core.security import create_access_token
from app.schemas.application import ApplicationCreate, ApplicationCreated
from app.services.application_settings import ApplicationSettingsRepository
from app.services.applications import ApplicationRepository, DuplicateApplicationError
from app.services.approvals import ApplicationApprovalService, ExistingParticipantError
from app.services.auth import ParticipantAccountRepository
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


def _application_settings_repository(request: Request) -> ApplicationSettingsRepository | None:
    return getattr(request.app.state, "application_settings_repository", None)


def _participant_audience(school_level: str | None) -> str:
    return "elementary" if school_level == "초등" else "secondary"


@router.post("", response_model=ApplicationCreated, status_code=status.HTTP_201_CREATED)
async def create_application(application: ApplicationCreate, request: Request) -> ApplicationCreated:
    participant_repository = _participant_repository(request)
    if (
        participant_repository is not None
        and await participant_repository.find_by_phone(application.phone) is not None
    ):
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

    settings_repository = _application_settings_repository(request)
    if settings_repository is None or not await settings_repository.auto_approval_enabled():
        return ApplicationCreated(applicationId=application_id, status="pending")

    if participant_repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="승인 서비스를 준비 중입니다.")
    try:
        approved_count = await ApplicationApprovalService(_repository(request), participant_repository).approve(
            [application_id]
        )
    except ExistingParticipantError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 등록된 참여자 휴대폰 번호입니다: {', '.join(error.phone_numbers)}",
        ) from error
    account = await participant_repository.find_by_phone(application.phone)
    if approved_count != 1 or account is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="자동 승인 처리를 완료하지 못했습니다."
        )

    app_settings = request.app.state.settings
    if app_settings.jwt_secret is None or len(app_settings.jwt_secret.encode()) < 32:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="인증 서비스를 준비 중입니다.")
    return ApplicationCreated(
        applicationId=application_id,
        status="approved",
        accessToken=create_access_token(
            account.participant_id,
            "participant",
            app_settings.jwt_secret,
            app_settings.participant_jwt_expiration_minutes,
        ),
        tokenType="bearer",
        role="participant",
        needsPasswordChange=account.must_change_password,
        audience=_participant_audience(account.school_level),
        chatConsent=account.chat_consent,
    )
