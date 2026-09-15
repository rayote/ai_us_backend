from __future__ import annotations

from app.api.auth import _participant_id
from app.api.survey import _job_repository
from app.schemas.chat import ChatSubmissionAccepted, ChatSubmissionCreate
from app.services.auth import ParticipantAccountRepository
from app.services.chats import (
    ChatConsentRequiredError,
    ChatSubmissionRepository,
    ChatSubmissionService,
    ChatUploadFile,
    ChatUploadRepository,
    ChatUploadService,
    InvalidChatLinkError,
    InvalidChatUploadError,
)
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

router = APIRouter(prefix="/api/v1/chat-submissions", tags=["chat submissions"])


def _submission_service(request: Request) -> ChatSubmissionService:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="대화문 제출 서비스를 준비 중입니다."
        )
    return ChatSubmissionService(participants, _job_repository(request))


def _upload_service(request: Request) -> ChatUploadService:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    submissions: ChatSubmissionRepository | None = getattr(request.app.state, "chat_submission_repository", None)
    uploads: ChatUploadRepository | None = getattr(request.app.state, "chat_upload_repository", None)
    if participants is None or submissions is None or uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 제출 서비스를 준비 중입니다."
        )
    return ChatUploadService(participants, submissions, uploads)


@router.post("", response_model=ChatSubmissionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def submit_chat(
    submission: ChatSubmissionCreate,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> ChatSubmissionAccepted:
    try:
        job = await _submission_service(request).submit(participant_id, submission)
    except ChatConsentRequiredError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="대화문 제출 동의가 필요합니다.") from error
    except InvalidChatLinkError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="유효한 대화 공유 링크가 아닙니다."
        ) from error
    return ChatSubmissionAccepted(submissionId=job.id, status=job.status)


@router.post("/uploads", response_model=ChatSubmissionAccepted, status_code=status.HTTP_202_ACCEPTED)
async def submit_chat_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    tool: str = Form(..., min_length=1, max_length=40),
    submission_point: str = Form(..., alias="submissionPoint"),
    source_type: str = Form(..., alias="sourceType"),
    submission_id: str = Form(..., alias="submissionId", min_length=1, max_length=128),
    participant_id: str = Depends(_participant_id),
) -> ChatSubmissionAccepted:
    try:
        upload_files = [
            ChatUploadFile(
                filename=file.filename or "upload",
                content_type=file.content_type or "application/octet-stream",
                data=await file.read(ChatUploadService._MAX_FILE_BYTES + 1),
            )
            for file in files
        ]
        if submission_point not in {"afterRound1", "afterRound4"}:
            raise InvalidChatUploadError("제출 시점이 올바르지 않습니다.")
        await _upload_service(request).submit(participant_id, submission_point, source_type, tool, upload_files)
    except ChatConsentRequiredError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="대화문 제출 동의가 필요합니다.") from error
    except InvalidChatUploadError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    return ChatSubmissionAccepted(submissionId=submission_id, status="completed")
