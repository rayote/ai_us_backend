from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from app.api.auth import _participant_id
from app.api.applications import participation_settings
from app.api.survey import _survey_status
from app.schemas.chat import (
    ChatSubmissionAccepted,
    ChatSubmissionCreate,
    ChatSubmissionSummary,
)
from app.services.auth import ParticipantAccountRepository
from app.services.chats import (
    ChatConsentRequiredError,
    ChatSubmissionManagementService,
    ChatSubmissionRepository,
    ChatUploadFile,
    ChatUploadRepository,
    ChatUploadService,
    InvalidChatUploadError,
    submission_summary,
)
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)

router = APIRouter(prefix="/api/v1/chat-submissions", tags=["chat submissions"])
PARTICIPANT_CHAT_TOOLS = {"zeta", "crack", "other"}
CHARACTER_CHAT_ONLY = "지금은 캐릭터 챗봇 서비스의 대화문만 제출이 가능해요."


async def _require_completed_survey(request: Request, participant_id: str) -> int:
    participants = getattr(request.app.state, "participant_account_repository", None)
    participant = (
        await participants.find_by_id(participant_id)
        if participants is not None
        else None
    )
    if participant is None or not participant.chat_consent:
        raise HTTPException(status_code=403, detail="대화문 제출 동의가 필요합니다.")
    survey_status = await _survey_status(request, participant_id)
    completed_rounds = set(survey_status["completedRounds"]) & {1, 2, 3}
    if not completed_rounds:
        raise HTTPException(
            status_code=403, detail="설문을 한 회차 이상 완료한 후 제출할 수 있습니다."
        )
    if survey_status["participationPaused"] and not survey_status["chatSubmissionAllowed"]:
        raise HTTPException(
            status_code=403, detail=survey_status["chatSubmissionUnavailableReason"]
        )
    return max(completed_rounds)


async def _chat_submission_participant(
    request: Request, participant_id: str = Depends(_participant_id)
) -> AsyncIterator[tuple[str, int]]:
    if not (await participation_settings(request))["participation_paused"]:
        completed_round = await _require_completed_survey(request, participant_id)
        yield participant_id, completed_round
        return
    participants = getattr(request.app.state, "participant_account_repository", None)
    if participants is None:
        raise HTTPException(status_code=503, detail="대화문 제출 서비스를 준비 중입니다.")
    token = str(uuid4())
    if not await participants.acquire_chat_submission_lock(participant_id, token):
        raise HTTPException(status_code=409, detail="대화문 제출을 처리 중입니다. 잠시 후 제출 내역을 확인해 주세요. 문제가 계속되면 연구팀에 문의해 주세요.")
    try:
        completed_round = await _require_completed_survey(request, participant_id)
        yield participant_id, completed_round
    finally:
        await participants.release_chat_submission_lock(participant_id, token)


def _upload_service(request: Request) -> ChatUploadService:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    submissions: ChatSubmissionRepository | None = getattr(
        request.app.state, "chat_submission_repository", None
    )
    uploads: ChatUploadRepository | None = getattr(
        request.app.state, "chat_upload_repository", None
    )
    if participants is None or submissions is None or uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="파일 제출 서비스를 준비 중입니다.",
        )
    return ChatUploadService(participants, submissions, uploads)


def _management_service(request: Request) -> ChatSubmissionManagementService:
    submissions: ChatSubmissionRepository | None = getattr(
        request.app.state, "chat_submission_repository", None
    )
    uploads: ChatUploadRepository | None = getattr(
        request.app.state, "chat_upload_repository", None
    )
    if submissions is None or uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대화문 관리 서비스를 준비 중입니다.",
        )
    return ChatSubmissionManagementService(submissions, uploads)


@router.post(
    "", response_model=ChatSubmissionAccepted, status_code=status.HTTP_202_ACCEPTED
)
async def submit_chat(
    submission: ChatSubmissionCreate,
    request: Request,
    eligibility: tuple[str, int] = Depends(_chat_submission_participant),
) -> ChatSubmissionAccepted:
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=CHARACTER_CHAT_ONLY,
    )


@router.post(
    "/uploads",
    response_model=ChatSubmissionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_chat_upload(
    request: Request,
    files: list[UploadFile] = File(...),
    tool: str = Form(..., min_length=1, max_length=40),
    submission_point: str = Form(..., alias="submissionPoint"),
    source_type: str = Form(..., alias="sourceType"),
    submission_id: str = Form(..., alias="submissionId", min_length=1, max_length=128),
    eligibility: tuple[str, int] = Depends(_chat_submission_participant),
) -> ChatSubmissionAccepted:
    participant_id, completed_round = eligibility
    try:
        if tool not in PARTICIPANT_CHAT_TOOLS or source_type != "image":
            raise InvalidChatUploadError(CHARACTER_CHAT_ONLY)
        upload_files = [
            ChatUploadFile(
                filename=file.filename or "upload",
                content_type=file.content_type or "application/octet-stream",
                data=await file.read(ChatUploadService._MAX_FILE_BYTES + 1),
            )
            for file in files
        ]
        if submission_point not in {
            "afterRound1",
            "afterRound2",
            "afterRound3",
            "afterRound4",
        }:
            raise InvalidChatUploadError("제출 시점이 올바르지 않습니다.")
        await _upload_service(request).submit(
            participant_id,
            f"afterRound{completed_round}",
            source_type,
            tool,
            submission_id,
            upload_files,
        )
    except ChatConsentRequiredError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="대화문 제출 동의가 필요합니다.",
        ) from error
    except InvalidChatUploadError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return ChatSubmissionAccepted(submissionId=submission_id, status="completed")


@router.get("/mine", response_model=list[ChatSubmissionSummary])
async def list_my_chat_submissions(
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> list[ChatSubmissionSummary]:
    submissions: ChatSubmissionRepository | None = getattr(
        request.app.state, "chat_submission_repository", None
    )
    if submissions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대화문 관리 서비스를 준비 중입니다.",
        )
    return [
        ChatSubmissionSummary.model_validate(submission_summary(submission))
        for submission in await submissions.list_for_participant(participant_id)
    ]


@router.post("/{submission_id}/deletion-request", response_model=ChatSubmissionSummary)
async def request_chat_submission_deletion(
    submission_id: str,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> ChatSubmissionSummary:
    if not await _management_service(request).request_deletion(
        submission_id, participant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="삭제 요청할 제출 내역을 찾을 수 없습니다.",
        )
    submissions: ChatSubmissionRepository = request.app.state.chat_submission_repository
    submission = next(
        (
            item
            for item in await submissions.list_for_participant(participant_id)
            if item.submission_id == submission_id
        ),
        None,
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="삭제 요청할 제출 내역을 찾을 수 없습니다.",
        )
    return ChatSubmissionSummary.model_validate(submission_summary(submission))


@router.post("/{submission_id}/restore", response_model=ChatSubmissionSummary)
async def restore_chat_submission(
    submission_id: str,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> ChatSubmissionSummary:
    if not await _management_service(request).restore_deletion(
        submission_id, participant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="되돌릴 삭제 요청 내역을 찾을 수 없습니다.",
        )
    submissions: ChatSubmissionRepository = request.app.state.chat_submission_repository
    submission = next(
        (
            item
            for item in await submissions.list_for_participant(participant_id)
            if item.submission_id == submission_id
        ),
        None,
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="되돌릴 삭제 요청 내역을 찾을 수 없습니다.",
        )
    return ChatSubmissionSummary.model_validate(submission_summary(submission))
