from __future__ import annotations

from typing import Literal

from app.api.auth import require_researcher
from app.schemas.application import ApplicationApproval, ApplicationApprovalCompleted, ApplicationRecord
from app.schemas.imports import ParticipantImportResult
from app.schemas.reporting import NonparticipantReport, ParticipationStatus
from app.services.applications import ApplicationRepository
from app.services.approvals import ApplicationApprovalService, ExistingParticipantError
from app.services.auth import ParticipantAccountRepository
from app.services.chats import ChatSubmissionRepository, chat_submissions_to_csv
from app.services.imports import ParticipantImportService
from app.services.reporting import ResearcherReportingService
from app.services.surveys import SurveyDefinitionRepository, SurveyResponseRepository, survey_responses_to_csv
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
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


def _participant_import_service(request: Request) -> ParticipantImportService:
    repository: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 등록 서비스를 준비 중입니다."
        )
    return ParticipantImportService(repository)


def _survey_definition_repository(request: Request) -> SurveyDefinitionRepository:
    repository = getattr(request.app.state, "survey_definition_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="설문 정의 서비스를 준비 중입니다."
        )
    return repository


def _survey_response_repository(request: Request) -> SurveyResponseRepository:
    repository = getattr(request.app.state, "survey_response_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="설문 결과 서비스를 준비 중입니다."
        )
    return repository


def _chat_submission_repository(request: Request) -> ChatSubmissionRepository:
    repository = getattr(request.app.state, "chat_submission_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="대화문 결과 서비스를 준비 중입니다."
        )
    return repository


def _reporting_service(request: Request) -> ResearcherReportingService:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    responses: SurveyResponseRepository | None = getattr(request.app.state, "survey_response_repository", None)
    if participants is None or responses is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여 현황 서비스를 준비 중입니다."
        )
    return ResearcherReportingService(participants, responses)


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
    try:
        approved_count = await _approval_service(request).approve(approval.application_ids)
    except ExistingParticipantError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 등록된 참여자 휴대폰 번호입니다: {', '.join(error.phone_numbers)}",
        ) from error
    return ApplicationApprovalCompleted(approvedCount=approved_count)


@router.post("/participants/imports", response_model=ParticipantImportResult)
async def import_participants(
    request: Request,
    file: UploadFile = File(...),
    _: str = Depends(require_researcher),
) -> ParticipantImportResult:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="CSV 파일만 업로드할 수 있습니다."
        )
    return await _participant_import_service(request).import_csv(await file.read())


@router.get("/participation-status", response_model=ParticipationStatus)
async def participation_status(
    request: Request,
    _: str = Depends(require_researcher),
) -> ParticipationStatus:
    return await _reporting_service(request).participation_status()


@router.get("/nonparticipants", response_model=NonparticipantReport)
async def nonparticipants(
    survey_round: int,
    survey_version: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> NonparticipantReport:
    return await _reporting_service(request).nonparticipants(survey_round, survey_version)


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


@router.get("/exports/chat-submissions")
async def export_chat_submissions(
    submission_point: Literal["afterRound1", "afterRound4"] | None = None,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    request: Request = None,
    _: str = Depends(require_researcher),
) -> Response:
    participants: ParticipantAccountRepository | None = getattr(request.app.state, "participant_account_repository", None)
    if participants is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 정보를 준비 중입니다.")
    profiles = {
        participant.participant_id: (participant.name, participant.school_level, participant.grade)
        for participant in await participants.list_participants()
    }
    submissions = await _chat_submission_repository(request).list_submissions(submission_point)
    if school_level is not None:
        submissions = [submission for submission in submissions if profiles.get(submission.participant_id, (None, None, None))[1] == school_level]
    csv_text = chat_submissions_to_csv(submissions, profiles)
    point_name = submission_point or "all"
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chat-submissions-{point_name}.csv"'},
    )
