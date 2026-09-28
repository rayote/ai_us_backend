from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from app.api.auth import require_researcher
from app.schemas.abuse_review import (
    AbuseReviewGroupStatusUpdate,
    AbuseReviewReport,
    AbuseReviewStatus,
    AbuseReviewStatusUpdate,
)
from app.schemas.application import (
    ApplicationApproval,
    ApplicationApprovalCompleted,
    ApplicationRecord,
    ApplicationSettings,
    ChatConsentUpdate,
)
from app.schemas.chat import (
    ChatAttachment,
    ChatDownloadJobAccepted,
    ChatDownloadJobCreate,
    ChatDownloadJobStatus,
    ChatImportComplete,
    ChatImportCreate,
    ChatImportSessionStatus,
    ChatSubmissionDeleted,
    ChatSubmissionPreview,
    ChatSubmissionRecord,
    ChatSubmissionReview,
    ChatSubmissionReviewUpdate,
    ChatSubmissionSummary,
    ParsedTranscript,
    TranscriptParsePreview,
    TranscriptParseRequestAccepted,
    TranscriptParseRun,
)
from app.schemas.imports import ParticipantImportResult
from app.schemas.reporting import (
    IncompleteParticipantReport,
    NonparticipantReport,
    ParticipationStatus,
)
from app.schemas.survey import SurveyDefinitionSummary, SurveyResponsePreview
from app.services.abuse_review import AbuseReviewService
from app.services.abuse_review_status import (
    AbuseReviewStatusRepository,
    abuse_candidate_key,
)
from app.services.application_settings import ApplicationSettingsRepository
from app.services.applications import ApplicationRepository
from app.services.approvals import ApplicationApprovalService, ExistingParticipantError
from app.services.auth import ParticipantAccountRepository
from app.services.chat_downloads import ChatDownloadArtifactRepository
from app.services.chat_imports import (
    CHAT_IMPORT_TOOL_TYPES,
    IMAGE_CONTENT_TYPES,
    MAX_IMAGE_BYTES,
    ChatImportRepository,
    ChatImportSession,
    inspect_chat_import,
)
from app.services.chats import (
    ChatSubmissionManagementService,
    ChatSubmissionRepository,
    ChatUploadRepository,
    build_chat_archive,
    chat_submissions_to_csv,
    submission_summary,
)
from app.services.imports import ParticipantImportService
from app.services.jobs import JobCreate, JobRepository
from app.services.reporting import ResearcherReportingService
from app.services.surveys import (
    SurveyDefinitionRepository,
    SurveyResponseRepository,
    survey_responses_to_csv,
)
from app.services.transcript_runs import TranscriptParseRunRepository, normalized_to_csv
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response

router = APIRouter(prefix="/api/v1/researcher", tags=["researcher"])


def _application_repository(request: Request) -> ApplicationRepository:
    repository = getattr(request.app.state, "application_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="신청 관리 서비스를 준비 중입니다.",
        )
    return repository


def _application_settings_repository(request: Request) -> ApplicationSettingsRepository:
    repository = getattr(request.app.state, "application_settings_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="신청 설정 서비스를 준비 중입니다.",
        )
    return repository


def _approval_service(request: Request) -> ApplicationApprovalService:
    participant_repository: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participant_repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="승인 서비스를 준비 중입니다.",
        )
    return ApplicationApprovalService(
        _application_repository(request), participant_repository
    )


def _participant_import_service(request: Request) -> ParticipantImportService:
    repository: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여자 등록 서비스를 준비 중입니다.",
        )
    return ParticipantImportService(repository)


def _survey_definition_repository(request: Request) -> SurveyDefinitionRepository:
    repository = getattr(request.app.state, "survey_definition_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="설문 정의 서비스를 준비 중입니다.",
        )
    return repository


def _survey_response_repository(request: Request) -> SurveyResponseRepository:
    repository = getattr(request.app.state, "survey_response_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="설문 결과 서비스를 준비 중입니다.",
        )
    return repository


def _chat_submission_repository(request: Request) -> ChatSubmissionRepository:
    repository = getattr(request.app.state, "chat_submission_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대화문 결과 서비스를 준비 중입니다.",
        )
    return repository


def _chat_import_repository(request: Request) -> ChatImportRepository:
    repository = getattr(request.app.state, "chat_import_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대용량 파일 등록 서비스를 준비 중입니다.",
        )
    return repository


def _chat_import_status(session: ChatImportSession) -> ChatImportSessionStatus:
    return ChatImportSessionStatus(
        uploadId=session.upload_id,
        chunkSize=session.chunk_size,
        totalChunks=session.total_chunks,
        uploadedChunks=list(session.uploaded_chunks),
        status=session.status,
        submissionId=session.submission_id,
    )


def _chat_submission_management_service(
    request: Request,
) -> ChatSubmissionManagementService:
    uploads: ChatUploadRepository | None = getattr(
        request.app.state, "chat_upload_repository", None
    )
    if uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="파일 관리 서비스를 준비 중입니다.",
        )
    return ChatSubmissionManagementService(
        _chat_submission_repository(request), uploads
    )


def _chat_download_artifact_repository(
    request: Request,
) -> ChatDownloadArtifactRepository:
    repository = getattr(request.app.state, "chat_download_artifact_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="파일 다운로드 서비스를 준비 중입니다.",
        )
    return repository


def _job_repository(request: Request) -> JobRepository:
    repository = getattr(request.app.state, "job_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="다운로드 작업 서비스를 준비 중입니다.",
        )
    return repository


def _transcript_parse_run_repository(request: Request) -> TranscriptParseRunRepository:
    repository = getattr(request.app.state, "transcript_parse_run_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대화문 파싱 서비스를 준비 중입니다.",
        )
    return repository


def _reporting_service(request: Request) -> ResearcherReportingService:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    responses: SurveyResponseRepository | None = getattr(
        request.app.state, "survey_response_repository", None
    )
    if participants is None or responses is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여 현황 서비스를 준비 중입니다.",
        )
    chats: ChatSubmissionRepository | None = getattr(
        request.app.state, "chat_submission_repository", None
    )
    return ResearcherReportingService(participants, responses, chats)


def _abuse_review_service(request: Request) -> AbuseReviewService:
    participants = getattr(request.app.state, "participant_account_repository", None)
    responses = getattr(request.app.state, "survey_response_repository", None)
    definitions = getattr(request.app.state, "survey_definition_repository", None)
    if participants is None or responses is None or definitions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="어뷰징 검토 서비스를 준비 중입니다.",
        )
    return AbuseReviewService(participants, responses, definitions)


def _abuse_review_status_repository(request: Request) -> AbuseReviewStatusRepository:
    repository = getattr(request.app.state, "abuse_review_status_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="어뷰징 검토 상태 서비스를 준비 중입니다.",
        )
    return repository


@router.get("/activity-summary")
async def activity_summary(
    request: Request,
    _: str = Depends(require_researcher),
) -> dict[str, object]:
    sessions = getattr(request.app.state, "survey_session_repository", None)
    if sessions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="활성 분석 서비스를 준비 중입니다.",
        )
    summary = await sessions.activity_summary()
    metrics = getattr(request.app.state, "daily_metrics_repository", None)
    if metrics is not None:
        summary.update(await metrics.summary())
    return summary


@router.get("/abuse-review", response_model=AbuseReviewReport)
async def abuse_review(
    request: Request,
    survey_round: int | None = None,
    survey_version: str | None = None,
    pair_page: int = 1,
    pair_page_size: int = 200,
    speed_page: int = 1,
    pairing_page: int = 1,
    combined_page: int = 1,
    _: str = Depends(require_researcher),
) -> AbuseReviewReport:
    if (survey_round is None) != (survey_version is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="회차와 설문 버전을 함께 선택해 주세요.",
        )
    return await _abuse_review_service(request).report(
        survey_round,
        survey_version,
        pair_page,
        pair_page_size,
        speed_page,
        pairing_page,
        combined_page,
    )


@router.get("/abuse-review/statuses", response_model=list[AbuseReviewStatus])
async def abuse_review_statuses(
    survey_round: int,
    survey_version: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> list[AbuseReviewStatus]:
    return await _abuse_review_status_repository(request).list_statuses(
        survey_round, survey_version
    )


@router.put("/abuse-review/statuses", response_model=AbuseReviewStatus)
async def update_abuse_review_status(
    update: AbuseReviewStatusUpdate,
    survey_round: int,
    survey_version: str,
    request: Request,
    reviewer: str = Depends(require_researcher),
) -> AbuseReviewStatus:
    candidate_key = abuse_candidate_key(
        survey_round, survey_version, update.phone, update.paired_phone
    )
    repository = _abuse_review_status_repository(request)
    result = await repository.set_status(
        survey_round, survey_version, candidate_key, update.reviewed, reviewer
    )
    report = await _abuse_review_service(request).report(
        survey_round, survey_version, 1, 100000
    )
    statuses = await repository.list_statuses(survey_round, survey_version)
    reviewed_keys = {item.candidate_key for item in statuses if item.reviewed}
    group_statuses = {
        item.candidate_key: item for item in statuses if item.member_phones
    }
    for group in report.groups:
        group_phones = set(group.phones)
        group_pair_keys = {
            candidate.candidate_key
            for candidate in report.pairing_candidates + report.combined_candidates
            if candidate.paired_phone
            and {candidate.phone, candidate.paired_phone}.issubset(group_phones)
        }
        if not group_pair_keys:
            continue
        all_reviewed = group_pair_keys.issubset(reviewed_keys)
        current_group_status = group_statuses.get(group.group_key)
        if all_reviewed or (current_group_status and current_group_status.reviewed):
            await repository.set_status(
                survey_round,
                survey_version,
                group.group_key,
                all_reviewed,
                reviewer,
                group.phones,
            )
    return result


@router.put("/abuse-review/group-statuses", response_model=AbuseReviewStatus)
async def update_abuse_review_group_status(
    update: AbuseReviewGroupStatusUpdate,
    survey_round: int,
    survey_version: str,
    request: Request,
    reviewer: str = Depends(require_researcher),
) -> AbuseReviewStatus:
    status_repository = _abuse_review_status_repository(request)
    report = await _abuse_review_service(request).report(
        survey_round, survey_version, 1, 100000
    )
    member_phones = set(update.phones)
    for candidate in report.pairing_candidates + report.combined_candidates:
        if candidate.paired_phone and {
            candidate.phone,
            candidate.paired_phone,
        }.issubset(member_phones):
            await status_repository.set_status(
                survey_round,
                survey_version,
                candidate.candidate_key,
                update.reviewed,
                reviewer,
            )
    return await status_repository.set_status(
        survey_round,
        survey_version,
        update.group_key,
        update.reviewed,
        reviewer,
        sorted(member_phones),
    )


@router.get("/applications", response_model=list[ApplicationRecord])
async def list_applications(
    school_level: Literal["elementary", "middle", "high"] | None = None,
    _: str = Depends(require_researcher),
    request: Request = None,
) -> list[ApplicationRecord]:
    records = await _application_repository(request).list_applications(school_level)
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        return records
    participant_list = await participants.list_participants() or []
    live_consent = {
        participant.phone: participant.chat_consent for participant in participant_list
    }
    return [
        record.model_copy(
            update={
                "chat_consent": live_consent.get(record.phone, record.consents.chat)
            }
        )
        for record in records
    ]


@router.post("/applications/approve", response_model=ApplicationApprovalCompleted)
async def approve_applications(
    approval: ApplicationApproval,
    request: Request,
    _: str = Depends(require_researcher),
) -> ApplicationApprovalCompleted:
    try:
        approved_count = await _approval_service(request).approve(
            approval.application_ids
        )
    except ExistingParticipantError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 등록된 참여자 휴대폰 번호입니다: {', '.join(error.phone_numbers)}",
        ) from error
    return ApplicationApprovalCompleted(approvedCount=approved_count)


@router.put("/applications/{application_id}/chat-consent")
async def update_application_chat_consent(
    application_id: str,
    update: ChatConsentUpdate,
    request: Request,
    _: str = Depends(require_researcher),
) -> dict[str, object]:
    applications = await _application_repository(request).list_applications()
    application = next(
        (item for item in applications if item.application_id == application_id), None
    )
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="신청 정보를 찾을 수 없습니다.",
        )
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여자 정보를 준비 중입니다.",
        )
    participant = await participants.find_by_phone(application.phone)
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="승인된 참여자 계정을 찾을 수 없습니다.",
        )
    if not await participants.set_chat_consent(
        participant.participant_id, update.chat_consent
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대화문 동의를 변경하지 못했습니다.",
        )
    if not await _application_repository(request).set_chat_consent(
        application_id, update.chat_consent
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="신청 정보의 대화문 동의를 동기화하지 못했습니다.",
        )
    return {"applicationId": application_id, "chatConsent": update.chat_consent}


@router.get("/application-settings", response_model=ApplicationSettings)
async def get_application_settings(
    request: Request,
    _: str = Depends(require_researcher),
) -> ApplicationSettings:
    return ApplicationSettings(
        autoApproval=await _application_settings_repository(
            request
        ).auto_approval_enabled()
    )


@router.post("/participants/imports", response_model=ParticipantImportResult)
async def import_participants(
    request: Request,
    file: UploadFile = File(...),
    _: str = Depends(require_researcher),
) -> ParticipantImportResult:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CSV 파일만 업로드할 수 있습니다.",
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
    return await _reporting_service(request).nonparticipants(
        survey_round, survey_version
    )


@router.get("/incomplete-participants", response_model=IncompleteParticipantReport)
async def incomplete_participants(
    category: Literal["survey", "chat"],
    criterion: str,
    request: Request,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    _: str = Depends(require_researcher),
) -> IncompleteParticipantReport:
    if category == "survey":
        try:
            survey_round = int(criterion)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="설문 회차가 올바르지 않습니다.",
            ) from error
        if survey_round < 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="설문 회차가 올바르지 않습니다.",
            )
        criterion = str(survey_round)
    elif criterion not in {"afterRound1", "afterRound4"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="대화문 제출 시점이 올바르지 않습니다.",
        )
    return await _reporting_service(request).incomplete_participants(
        category, criterion, school_level
    )


@router.get("/survey-definitions", response_model=list[SurveyDefinitionSummary])
async def list_survey_definitions(
    request: Request,
    _: str = Depends(require_researcher),
) -> list[SurveyDefinitionSummary]:
    return await _survey_definition_repository(request).list_definitions()


@router.get("/exports/survey-responses")
async def export_survey_responses(
    survey_round: int,
    survey_version: str,
    request: Request,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    _: str = Depends(require_researcher),
) -> Response:
    definition = await _survey_definition_repository(request).get(
        survey_round, survey_version
    )
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="설문 정의를 찾을 수 없습니다.",
        )
    responses = await _survey_response_repository(request).list_responses(
        survey_round, survey_version
    )
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여자 정보를 준비 중입니다.",
        )
    profiles = {
        participant.participant_id: (
            participant.phone,
            participant.school_level,
            participant.grade,
        )
        for participant in await participants.list_participants()
    }
    if school_level is not None:
        responses = [
            response
            for response in responses
            if profiles.get(response.participant_id, ("-", None, None))[1]
            == school_level
        ]
    filename = _survey_export_filename(definition, datetime.now(UTC))
    return Response(
        content="\ufeff" + survey_responses_to_csv(definition, responses, profiles),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _survey_export_filename(definition, exported_at: datetime) -> str:
    audience = {"elementary": "elem", "secondary": "secondary"}.get(
        definition.audience, "all"
    )
    part = definition.spec.get("part") if isinstance(definition.spec, dict) else None
    part_label = f"part{part}" if isinstance(part, int) and part > 0 else "part"
    timestamp = exported_at.astimezone(ZoneInfo("Asia/Seoul")).strftime(
        "%Y-%m-%d_%H-%M-%S"
    )
    return f"T{definition.survey_round}_{audience}_{part_label}_{timestamp}.csv"


def _kst_filename_timestamp(exported_at: datetime | None = None) -> str:
    return (
        (exported_at or datetime.now(UTC))
        .astimezone(ZoneInfo("Asia/Seoul"))
        .strftime("%Y-%m-%d_%H-%M-%S")
    )


@router.get("/survey-response-previews", response_model=list[SurveyResponsePreview])
async def survey_response_previews(
    survey_round: int,
    survey_version: str,
    request: Request,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    _: str = Depends(require_researcher),
) -> list[SurveyResponsePreview]:
    responses = await _survey_response_repository(request).list_responses(
        survey_round, survey_version
    )
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여자 정보를 준비 중입니다.",
        )
    profiles = {
        participant.participant_id: (
            participant.phone,
            participant.school_level,
            participant.grade,
        )
        for participant in await participants.list_participants()
    }
    if school_level is not None:
        responses = [
            response
            for response in responses
            if profiles.get(response.participant_id, ("-", None, None))[1]
            == school_level
        ]
    return [
        SurveyResponsePreview(
            phone=profiles.get(response.participant_id, ("-", None, None))[0],
            schoolLevel=profiles.get(response.participant_id, ("-", None, None))[1],
            grade=profiles.get(response.participant_id, ("-", None, None))[2],
            surveyRound=response.survey_round,
            surveyVersion=response.survey_version,
            submittedAt=response.submitted_at,
        )
        for response in responses[:10]
    ]


@router.get("/exports/chat-submissions")
async def export_chat_submissions(
    submission_point: Literal["afterRound1", "afterRound4"] | None = None,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    submission_ids: list[str] | None = Query(default=None, alias="submission_id"),
    request: Request = None,
    _: str = Depends(require_researcher),
) -> Response:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여자 정보를 준비 중입니다.",
        )
    profiles = {
        participant.participant_id: (
            participant.name,
            participant.school_level,
            participant.grade,
        )
        for participant in await participants.list_participants()
    }
    submissions = await _chat_submission_repository(request).list_submissions(
        submission_point
    )
    if submission_ids:
        selected_ids = set(submission_ids)
        submissions = [
            submission
            for submission in submissions
            if submission.submission_id in selected_ids
        ]
    if school_level is not None:
        submissions = [
            submission
            for submission in submissions
            if profiles.get(submission.participant_id, (None, None, None))[1]
            == school_level
        ]
    csv_text = chat_submissions_to_csv(submissions, profiles)
    point_name = submission_point or "all"
    timestamp = _kst_filename_timestamp()
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="chat-submissions-{point_name}_{timestamp}.csv"'
        },
    )


@router.get("/chat-submission-previews", response_model=list[ChatSubmissionPreview])
async def chat_submission_previews(
    request: Request,
    submission_point: Literal["afterRound1", "afterRound4"] | None = None,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    review_filter: Literal[
        "all",
        "unreviewed",
        "reviewed",
        "incentive_paid",
        "excluded",
        "duplicate",
        "other",
    ] = "unreviewed",
    _: str = Depends(require_researcher),
) -> list[ChatSubmissionPreview]:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="참여자 정보를 준비 중입니다.",
        )
    profiles = {
        participant.participant_id: (
            participant.phone,
            participant.school_level,
            participant.grade,
        )
        for participant in await participants.list_participants()
    }
    submissions = await _chat_submission_repository(request).list_submissions(
        submission_point
    )
    rows: list[ChatSubmissionPreview] = []
    for submission in reversed(submissions):
        profile = profiles.get(submission.participant_id, ("-", None, None))
        if school_level is not None and profile[1] != school_level:
            continue
        if review_filter == "unreviewed" and submission.review is not None:
            continue
        if review_filter == "reviewed" and submission.review is None:
            continue
        if review_filter not in ("all", "unreviewed", "reviewed") and (
            submission.review is None or submission.review.status != review_filter
        ):
            continue
        rows.append(
            ChatSubmissionPreview(
                submissionId=submission.submission_id,
                participantPhone=profile[0],
                schoolLevel=profile[1],
                grade=profile[2],
                submissionPoint=submission.submission_point,
                sourceType=submission.source_type,
                tool=submission.tool,
                filenames=[
                    attachment.filename for attachment in submission.attachments
                ],
                attachmentCount=len(submission.attachments),
                parseStatus=submission.transcript.status,
                submittedAt=submission.submitted_at,
                review=submission.review,
            )
        )
    return rows[:100]


@router.patch(
    "/chat-submissions/{submission_id}/review", response_model=ChatSubmissionReview
)
async def update_chat_submission_review(
    submission_id: str,
    payload: ChatSubmissionReviewUpdate,
    request: Request,
    researcher_id: str = Depends(require_researcher),
) -> ChatSubmissionReview:
    review = ChatSubmissionReview(
        status=payload.status,
        note=payload.note,
        updatedAt=datetime.now(UTC),
        updatedBy=researcher_id,
    )
    if not await _chat_submission_repository(request).update_review(
        submission_id, review
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="대화문 제출 내역을 찾을 수 없습니다.",
        )
    return review


@router.delete(
    "/chat-submissions/{submission_id}/review", status_code=status.HTTP_204_NO_CONTENT
)
async def clear_chat_submission_review(
    submission_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    if not await _chat_submission_repository(request).clear_review(submission_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="대화문 제출 내역을 찾을 수 없습니다.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/chat-submissions/files/{submission_id}/download")
async def download_chat_submission_files(
    submission_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    repository = _chat_submission_repository(request)
    submissions = await repository.list_submissions()
    submissions.extend(await repository.list_submissions(status="deletion_requested"))
    submission = next(
        (item for item in submissions if item.submission_id == submission_id), None
    )
    if submission is None or not submission.attachments:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="다운로드할 제출 파일을 찾을 수 없습니다.",
        )
    uploads: ChatUploadRepository | None = getattr(
        request.app.state, "chat_upload_repository", None
    )
    if uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="파일 다운로드 서비스를 준비 중입니다.",
        )
    fd, temp_name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    archive_path = Path(temp_name)
    try:
        await build_chat_archive(
            [submission], uploads, archive_path, include_deletion_requested=True
        )
        content = archive_path.read_bytes()
    finally:
        archive_path.unlink(missing_ok=True)
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="chat-submission-{submission_id}_{_kst_filename_timestamp()}.zip"'
            )
        },
    )


@router.post(
    "/chat-submissions/download-jobs",
    response_model=ChatDownloadJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_chat_download_job(
    request_data: ChatDownloadJobCreate,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatDownloadJobAccepted:
    job_key = os.urandom(16).hex()
    job = await _job_repository(request).enqueue(
        JobCreate(
            job_type="chat_download",
            idempotency_key=job_key,
            payload={
                "jobKey": job_key,
                "submissionIds": request_data.submission_ids,
                "submissionPoint": request_data.submission_point,
                "schoolLevel": request_data.school_level,
                "reviewFilter": request_data.review_filter,
            },
        )
    )
    return ChatDownloadJobAccepted(jobId=job.id, status=job.status)


@router.get(
    "/chat-submissions/download-jobs/{job_id}", response_model=ChatDownloadJobStatus
)
async def get_chat_download_job(
    job_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatDownloadJobStatus:
    job = await _job_repository(request).get(job_id)
    if job is None or job.job_type != "chat_download":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="다운로드 작업을 찾을 수 없습니다.",
        )
    artifact = (
        await _chat_download_artifact_repository(request).get(job.idempotency_key)
        if job.status == "completed"
        else None
    )
    return ChatDownloadJobStatus(
        jobId=job.id,
        status=job.status,
        error=job.error,
        downloadUrl=(
            f"/api/v1/researcher/chat-submissions/download-jobs/{job.id}/file"
            if artifact
            else None
        ),
    )


@router.get(
    "/chat-submissions/download-jobs/{job_id}/file", name="download_chat_job_file"
)
async def download_chat_job_file(
    job_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    job = await _job_repository(request).get(job_id)
    if job is None or job.job_type != "chat_download":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="다운로드 작업을 찾을 수 없습니다.",
        )
    artifact = await _chat_download_artifact_repository(request).get(
        job.idempotency_key
    )
    uploads: ChatUploadRepository | None = getattr(
        request.app.state, "chat_download_upload_repository", None
    )
    if artifact is None or uploads is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="다운로드 파일이 아직 준비되지 않았습니다.",
        )
    filename, content, _ = await uploads.read_bytes(artifact.file_id)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/chat-submissions/transcript-download-jobs",
    response_model=ChatDownloadJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_transcript_download_job(
    request_data: ChatDownloadJobCreate,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatDownloadJobAccepted:
    job_key = os.urandom(16).hex()
    job = await _job_repository(request).enqueue(
        JobCreate(
            job_type="transcript_download",
            idempotency_key=job_key,
            payload={
                "jobKey": job_key,
                "submissionIds": request_data.submission_ids,
                "submissionPoint": request_data.submission_point,
                "schoolLevel": request_data.school_level,
                "reviewFilter": request_data.review_filter,
            },
        )
    )
    return ChatDownloadJobAccepted(jobId=job.id, status=job.status)


@router.get(
    "/chat-submissions/transcript-download-jobs/{job_id}",
    response_model=ChatDownloadJobStatus,
)
async def get_transcript_download_job(
    job_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatDownloadJobStatus:
    job = await _job_repository(request).get(job_id)
    if job is None or job.job_type != "transcript_download":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="대화문 CSV 작업을 찾을 수 없습니다.",
        )
    artifact = (
        await _chat_download_artifact_repository(request).get(job.idempotency_key)
        if job.status == "completed"
        else None
    )
    return ChatDownloadJobStatus(
        jobId=job.id,
        status=job.status,
        error=job.error,
        downloadUrl=(
            f"/api/v1/researcher/chat-submissions/transcript-download-jobs/{job.id}/file"
            if artifact
            else None
        ),
    )


@router.get("/chat-submissions/transcript-download-jobs/{job_id}/file")
async def download_transcript_job_file(
    job_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    job = await _job_repository(request).get(job_id)
    if job is None or job.job_type != "transcript_download":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="대화문 CSV 작업을 찾을 수 없습니다.",
        )
    artifact = await _chat_download_artifact_repository(request).get(
        job.idempotency_key
    )
    uploads: ChatUploadRepository | None = getattr(
        request.app.state, "chat_download_upload_repository", None
    )
    if artifact is None or uploads is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="다운로드 파일이 아직 준비되지 않았습니다.",
        )
    filename, content, _ = await uploads.read_bytes(artifact.file_id)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/chat-submissions/files", response_model=list[ChatSubmissionSummary])
async def list_chat_submission_files(
    request: Request,
    status_filter: Literal["active", "deletion_requested"] = "deletion_requested",
    _: str = Depends(require_researcher),
) -> list[ChatSubmissionSummary]:
    submissions = await _chat_submission_repository(request).list_submissions(
        status=status_filter
    )
    return [
        ChatSubmissionSummary.model_validate(
            submission_summary(submission, include_participant=True)
        )
        for submission in reversed(submissions)
    ]


@router.post(
    "/chat-submission-imports",
    response_model=ChatImportSessionStatus,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_submission_import(
    import_data: ChatImportCreate,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatImportSessionStatus:
    phone = "".join(character for character in import_data.phone if character.isdigit())
    if len(phone) != 11 or not phone.startswith("01"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="휴대폰 번호는 숫자 11자리여야 합니다.",
        )
    filename = Path(import_data.filename).name
    source_type = CHAT_IMPORT_TOOL_TYPES[import_data.tool]
    suffix = Path(filename).suffix.lower()
    image_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
    invalid_file = source_type == "file" and suffix != ".zip"
    invalid_image_suffix = suffix not in image_suffixes
    invalid_image_type = import_data.content_type not in IMAGE_CONTENT_TYPES
    invalid_image = source_type == "image" and (
        invalid_image_suffix or invalid_image_type
    )
    if filename != import_data.filename or invalid_file or invalid_image:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "선택한 서비스는 ZIP 파일을 등록해야 합니다."
                if source_type == "file"
                else "JPG, PNG, WEBP, HEIC 이미지만 등록할 수 있습니다."
            ),
        )
    if source_type == "image" and import_data.size > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="이미지 한 장은 20MB 미만이어야 합니다.",
        )
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    participant = await participants.find_by_phone(phone) if participants else None
    if participant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 휴대폰 번호의 참여자를 찾지 못했습니다.",
        )
    if not participant.chat_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI 대화문 제출에 동의한 참여자만 등록할 수 있습니다.",
        )
    session = await _chat_import_repository(request).create(
        participant.participant_id,
        import_data.submission_point,
        filename,
        import_data.tool,
        source_type,
        import_data.content_type,
        import_data.size,
    )
    return _chat_import_status(session)


@router.get(
    "/chat-submission-imports/{upload_id}", response_model=ChatImportSessionStatus
)
async def get_chat_submission_import(
    upload_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatImportSessionStatus:
    session = await _chat_import_repository(request).get(upload_id)
    if session is None:
        raise HTTPException(status_code=404, detail="업로드 세션을 찾을 수 없습니다.")
    return _chat_import_status(session)


@router.put(
    "/chat-submission-imports/{upload_id}/chunks/{chunk_number}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def upload_chat_submission_import_chunk(
    upload_id: str,
    chunk_number: int,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    session = await _chat_import_repository(request).get(upload_id)
    if session is None:
        raise HTTPException(status_code=404, detail="업로드 세션을 찾을 수 없습니다.")
    content_length = request.headers.get("content-length")
    if content_length is not None and int(content_length) > session.chunk_size:
        raise HTTPException(status_code=413, detail="chunk 크기 제한을 초과했습니다.")
    content = await request.body()
    try:
        await _chat_import_repository(request).put_chunk(
            upload_id, chunk_number, content
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/chat-submission-imports/{upload_id}/complete",
    response_model=ChatImportSessionStatus,
)
async def complete_chat_submission_import(
    upload_id: str,
    completion: ChatImportComplete,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatImportSessionStatus:
    imports = _chat_import_repository(request)
    upload_ids = list(dict.fromkeys(completion.upload_ids))
    if upload_id not in upload_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="완료할 업로드 목록이 올바르지 않습니다.",
        )
    sessions = [await imports.get(item) for item in upload_ids]
    if any(session is None for session in sessions):
        raise HTTPException(status_code=404, detail="업로드 세션을 찾을 수 없습니다.")
    resolved_sessions = [session for session in sessions if session is not None]
    first_session = resolved_sessions[0]
    if all(session.status == "completed" for session in resolved_sessions):
        return _chat_import_status(first_session)
    identity = {
        (
            session.participant_id,
            session.submission_point,
            session.tool,
            session.source_type,
        )
        for session in resolved_sessions
    }
    if len(identity) != 1 or any(
        session.status != "uploading" for session in resolved_sessions
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="서로 다른 업로드 세션은 함께 완료할 수 없습니다.",
        )
    if first_session.source_type == "file" and len(resolved_sessions) != 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="내보내기 ZIP은 한 개만 등록할 수 있습니다.",
        )
    total_size = sum(session.size for session in resolved_sessions)
    if first_session.source_type == "image" and total_size > 100 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="이미지 전체 크기는 100MB를 넘을 수 없습니다.",
        )

    temporary_paths: list[Path] = []
    try:
        file_sha256_values: list[str] = []
        for session in resolved_sessions:
            with tempfile.NamedTemporaryFile(
                suffix=Path(session.filename).suffix, delete=False
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary_paths.append(temporary_path)
            await imports.write_to_path(session.upload_id, temporary_path)
            file_sha256_values.append(
                inspect_chat_import(
                    temporary_path,
                    session.tool,
                    session.source_type,
                    session.content_type,
                )
            )
        digest = hashlib.sha256()
        for session, file_sha256 in zip(resolved_sessions, file_sha256_values):
            digest.update(session.filename.encode("utf-8"))
            digest.update(b"\0")
            digest.update(file_sha256.encode("ascii"))
            digest.update(b"\0")
        archive_sha256 = digest.hexdigest()
        if await imports.has_sha256(
            first_session.participant_id, archive_sha256, upload_ids
        ):
            for item in upload_ids:
                await imports.discard(item)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="같은 참여자의 동일한 파일 묶음이 이미 등록되어 있습니다.",
            )
        submission_id = f"researcher-dashboard-{upload_id}"
        attachments: list[ChatAttachment] = []
        for session in resolved_sessions:
            metadata = {
                "participant_id": session.participant_id,
                "submission_point": session.submission_point,
                "source_type": session.source_type,
                "tool": session.tool,
                "content_type": session.content_type,
                "client_submission_id": submission_id,
                "uploaded_by": "researcher-dashboard",
            }
            file_id = await imports.finalize_file(
                session.upload_id, archive_sha256, metadata
            )
            attachments.append(
                ChatAttachment(
                    fileId=file_id,
                    filename=session.filename,
                    contentType=session.content_type,
                    size=session.size,
                )
            )
        if not await imports.has_submission(submission_id):
            try:
                await _chat_submission_repository(request).create_submission(
                    ChatSubmissionRecord(
                        submissionId=submission_id,
                        participantId=first_session.participant_id,
                        submissionPoint=first_session.submission_point,
                        sourceType=first_session.source_type,
                        tool=first_session.tool,
                        rawInput=", ".join(
                            session.filename for session in resolved_sessions
                        ),
                        transcript=ParsedTranscript(
                            status="placeholder",
                            parserVersion="attachment-v1",
                            messages=[],
                            plainText="",
                            warnings=[
                                "원본 첨부 파일은 GridFS에 보관됩니다. 대화문 추출은 아직 수행되지 않았습니다."
                            ],
                        ),
                        submittedAt=datetime.now(UTC),
                        attachments=attachments,
                    )
                )
            except Exception:
                for item in upload_ids:
                    await imports.discard(item)
                raise
        for item in upload_ids:
            await imports.mark_completed(item, submission_id)
        completed = await imports.get(upload_id)
        if completed is None:
            raise RuntimeError("완료된 업로드 세션을 확인하지 못했습니다.")
        return _chat_import_status(completed)
    except HTTPException:
        raise
    except ValueError as error:
        for item in upload_ids:
            await imports.discard(item)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


@router.post(
    "/chat-submissions/{submission_id}/parse",
    response_model=TranscriptParseRequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_transcript_parse(
    submission_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> TranscriptParseRequestAccepted:
    submission_repository = _chat_submission_repository(request)
    submissions = await submission_repository.list_submissions()
    submission = next(
        (item for item in submissions if item.submission_id == submission_id), None
    )
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="대화문 제출을 찾을 수 없습니다.",
        )
    if submission.transcript.status != "placeholder":
        pending_transcript = submission.transcript.model_copy(
            update={"status": "placeholder"}
        )
        if not await submission_repository.update_transcript(
            submission_id, pending_transcript
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="대화문 파싱 상태를 갱신하지 못했습니다.",
            )
    runs = _transcript_parse_run_repository(request)
    existing = await runs.active(submission_id)
    if existing is not None:
        existing_job = await _job_repository(request).enqueue(
            JobCreate(
                job_type="transcript_parse",
                idempotency_key=f"{submission_id}:{existing.run_id}",
                payload={"submissionId": submission_id, "runId": existing.run_id},
            )
        )
        if existing_job.status in {"queued", "processing"}:
            return TranscriptParseRequestAccepted(
                runId=existing.run_id, jobId=existing_job.id, status=existing_job.status
            )
        await runs.fail(
            existing.run_id,
            "연결된 파싱 작업이 이미 종료되어 새 작업을 생성했습니다.",
            [],
        )
    run = await runs.create(submission_id, "adapter-router", "adapter-router-v1")
    job = await _job_repository(request).enqueue(
        JobCreate(
            job_type="transcript_parse",
            idempotency_key=f"{submission_id}:{run.run_id}",
            payload={"submissionId": submission_id, "runId": run.run_id},
        )
    )
    return TranscriptParseRequestAccepted(
        runId=run.run_id, jobId=job.id, status=job.status
    )


@router.get(
    "/chat-submissions/{submission_id}/parse-runs/{run_id}",
    response_model=TranscriptParseRun,
)
async def get_transcript_parse_run(
    submission_id: str,
    run_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> TranscriptParseRun:
    run = await _transcript_parse_run_repository(request).get(run_id)
    if run is None or run.submission_id != submission_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="파싱 실행 내역을 찾을 수 없습니다.",
        )
    return run


@router.get(
    "/chat-submissions/{submission_id}/latest-parse",
    response_model=TranscriptParsePreview,
)
async def get_latest_transcript_parse(
    submission_id: str, request: Request, _: str = Depends(require_researcher)
) -> TranscriptParsePreview:
    return TranscriptParsePreview(
        submissionId=submission_id,
        latestRun=await _transcript_parse_run_repository(request).latest(submission_id),
    )


@router.get("/chat-submissions/{submission_id}/latest-parse/download")
async def download_latest_transcript_parse(
    submission_id: str,
    format: Literal["json", "csv"] = "json",
    request: Request = None,
    _: str = Depends(require_researcher),
) -> Response:
    run = await _transcript_parse_run_repository(request).latest(submission_id)
    if run is None or run.normalized_json is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="완료된 파싱 결과를 찾을 수 없습니다.",
        )
    if format == "csv":
        return Response(
            "\ufeff" + normalized_to_csv(run.normalized_json),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="transcript-{submission_id}_{_kst_filename_timestamp()}.csv"'
                )
            },
        )
    return Response(
        json.dumps(run.normalized_json, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="transcript-{submission_id}_{_kst_filename_timestamp()}.json"'
            )
        },
    )


@router.delete(
    "/chat-submissions/files/{submission_id}", response_model=ChatSubmissionDeleted
)
async def delete_requested_chat_submission(
    submission_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatSubmissionDeleted:
    if not await _chat_submission_management_service(
        request
    ).delete_requested_submission(submission_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="삭제 요청된 파일 제출 내역을 찾을 수 없습니다.",
        )
    return ChatSubmissionDeleted(status="deleted")
