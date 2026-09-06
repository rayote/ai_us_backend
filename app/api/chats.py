from __future__ import annotations

from app.api.auth import _participant_id
from app.api.survey import _job_repository
from app.schemas.chat import ChatSubmissionAccepted, ChatSubmissionCreate
from app.services.auth import ParticipantAccountRepository
from app.services.chats import ChatConsentRequiredError, ChatSubmissionService, InvalidChatLinkError
from fastapi import APIRouter, Depends, HTTPException, Request, status

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
