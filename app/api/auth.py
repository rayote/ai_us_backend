from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError

from app.core.security import create_access_token, decode_access_token
from app.schemas.auth import AccessToken, ParticipantLogin, PasswordChange, PasswordChangeCompleted
from app.services.auth import InvalidCredentialsError, ParticipantAccountRepository, ParticipantAuthenticationService

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
bearer_scheme = HTTPBearer(auto_error=False)


def _service(request: Request) -> ParticipantAuthenticationService:
    repository = getattr(request.app.state, "participant_account_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="인증 서비스를 준비 중입니다.")
    return ParticipantAuthenticationService(repository)


def _settings(request: Request):
    secret = request.app.state.settings.jwt_secret
    if secret is None or len(secret.encode()) < 32:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="인증 서비스를 준비 중입니다.")
    return request.app.state.settings


def _participant_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> str:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="인증이 필요합니다.")
    try:
        claims = decode_access_token(credentials.credentials, _settings(request).jwt_secret)
    except InvalidTokenError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="유효하지 않은 인증 정보입니다.") from error
    if claims["role"] != "participant":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="참여자 권한이 필요합니다.")
    return claims["sub"]


@router.post("/participant/login", response_model=AccessToken)
async def participant_login(credentials: ParticipantLogin, request: Request) -> AccessToken:
    try:
        account = await _service(request).authenticate(credentials.phone, credentials.password)
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="휴대폰 번호 또는 비밀번호가 올바르지 않습니다.") from error

    settings = _settings(request)
    return AccessToken(
        accessToken=create_access_token(
            account.participant_id,
            "participant",
            settings.jwt_secret,
            settings.jwt_expiration_minutes,
        ),
        tokenType="bearer",
        role="participant",
        needsPasswordChange=account.must_change_password,
    )


@router.post("/participant/password", response_model=PasswordChangeCompleted)
async def change_participant_password(
    password_change: PasswordChange,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> PasswordChangeCompleted:
    try:
        await _service(request).change_password(
            participant_id,
            password_change.current_password,
            password_change.new_password,
        )
    except InvalidCredentialsError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="현재 비밀번호가 올바르지 않습니다.") from error
    return PasswordChangeCompleted(status="completed")