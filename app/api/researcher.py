from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from app.api.auth import require_researcher
from app.schemas.application import (
    ApplicationApproval,
    ApplicationApprovalCompleted,
    ApplicationRecord,
    ApplicationSettings,
    ChatConsentUpdate,
)
from app.schemas.chat import (
    ChatDownloadJobAccepted,
    ChatDownloadJobCreate,
    ChatDownloadJobStatus,
    ChatSubmissionDeleted,
    ChatSubmissionPreview,
    ChatSubmissionSummary,
)
from app.schemas.imports import ParticipantImportResult
from app.schemas.reporting import NonparticipantReport, ParticipationStatus
from app.schemas.survey import SurveyDefinitionSummary, SurveyResponsePreview
from app.services.application_settings import ApplicationSettingsRepository
from app.services.applications import ApplicationRepository
from app.services.approvals import ApplicationApprovalService, ExistingParticipantError
from app.services.auth import ParticipantAccountRepository
from app.services.chat_downloads import ChatDownloadArtifactRepository
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


def _application_settings_repository(request: Request) -> ApplicationSettingsRepository:
    repository = getattr(request.app.state, "application_settings_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="신청 설정 서비스를 준비 중입니다."
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


def _chat_submission_management_service(request: Request) -> ChatSubmissionManagementService:
    uploads: ChatUploadRepository | None = getattr(request.app.state, "chat_upload_repository", None)
    if uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 관리 서비스를 준비 중입니다."
        )
    return ChatSubmissionManagementService(_chat_submission_repository(request), uploads)


def _chat_download_artifact_repository(request: Request) -> ChatDownloadArtifactRepository:
    repository = getattr(request.app.state, "chat_download_artifact_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 다운로드 서비스를 준비 중입니다."
        )
    return repository


def _job_repository(request: Request) -> JobRepository:
    repository = getattr(request.app.state, "job_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="다운로드 작업 서비스를 준비 중입니다."
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
    records = await _application_repository(request).list_applications(school_level)
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        return records
    participant_list = await participants.list_participants() or []
    live_consent = {participant.phone: participant.chat_consent for participant in participant_list}
    return [
        record.model_copy(update={"chat_consent": live_consent.get(record.phone, record.consents.chat)})
        for record in records
    ]


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


@router.put("/applications/{application_id}/chat-consent")
async def update_application_chat_consent(
    application_id: str,
    update: ChatConsentUpdate,
    request: Request,
    _: str = Depends(require_researcher),
) -> dict[str, object]:
    applications = await _application_repository(request).list_applications()
    application = next((item for item in applications if item.application_id == application_id), None)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="신청 정보를 찾을 수 없습니다.")
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 정보를 준비 중입니다.")
    participant = await participants.find_by_phone(application.phone)
    if participant is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="승인된 참여자 계정을 찾을 수 없습니다.")
    if not await participants.set_chat_consent(participant.participant_id, update.chat_consent):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="대화문 동의를 변경하지 못했습니다."
        )
    if not await _application_repository(request).set_chat_consent(application_id, update.chat_consent):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="신청 정보의 대화문 동의를 동기화하지 못했습니다."
        )
    return {"applicationId": application_id, "chatConsent": update.chat_consent}


@router.get("/application-settings", response_model=ApplicationSettings)
async def get_application_settings(
    request: Request,
    _: str = Depends(require_researcher),
) -> ApplicationSettings:
    return ApplicationSettings(autoApproval=await _application_settings_repository(request).auto_approval_enabled())


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
    definition = await _survey_definition_repository(request).get(survey_round, survey_version)
    if definition is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="설문 정의를 찾을 수 없습니다.")
    responses = await _survey_response_repository(request).list_responses(survey_round, survey_version)
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 정보를 준비 중입니다.")
    profiles = {
        participant.participant_id: (participant.phone, participant.school_level, participant.grade)
        for participant in await participants.list_participants()
    }
    if school_level is not None:
        responses = [
            response
            for response in responses
            if profiles.get(response.participant_id, ("-", None, None))[1] == school_level
        ]
    filename = _survey_export_filename(definition, datetime.now(UTC))
    return Response(
        content="\ufeff" + survey_responses_to_csv(definition, responses, profiles),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _survey_export_filename(definition, exported_at: datetime) -> str:
    audience = {"elementary": "elem", "secondary": "secondary"}.get(definition.audience, "all")
    part = definition.spec.get("part") if isinstance(definition.spec, dict) else None
    part_label = f"part{part}" if isinstance(part, int) and part > 0 else "part"
    timestamp = exported_at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d_%H-%M-%S")
    return f"T{definition.survey_round}_{audience}_{part_label}_{timestamp}.csv"


@router.get("/survey-response-previews", response_model=list[SurveyResponsePreview])
async def survey_response_previews(
    survey_round: int,
    survey_version: str,
    request: Request,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    _: str = Depends(require_researcher),
) -> list[SurveyResponsePreview]:
    responses = await _survey_response_repository(request).list_responses(survey_round, survey_version)
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 정보를 준비 중입니다.")
    profiles = {
        participant.participant_id: (participant.phone, participant.school_level, participant.grade)
        for participant in await participants.list_participants()
    }
    if school_level is not None:
        responses = [
            response
            for response in responses
            if profiles.get(response.participant_id, ("-", None, None))[1] == school_level
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
    request: Request = None,
    _: str = Depends(require_researcher),
) -> Response:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 정보를 준비 중입니다.")
    profiles = {
        participant.participant_id: (participant.name, participant.school_level, participant.grade)
        for participant in await participants.list_participants()
    }
    submissions = await _chat_submission_repository(request).list_submissions(submission_point)
    if school_level is not None:
        submissions = [
            submission
            for submission in submissions
            if profiles.get(submission.participant_id, (None, None, None))[1] == school_level
        ]
    csv_text = chat_submissions_to_csv(submissions, profiles)
    point_name = submission_point or "all"
    return Response(
        content="\ufeff" + csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chat-submissions-{point_name}.csv"'},
    )


@router.get("/chat-submission-previews", response_model=list[ChatSubmissionPreview])
async def chat_submission_previews(
    request: Request,
    submission_point: Literal["afterRound1", "afterRound4"] | None = None,
    school_level: Literal["초등", "중등", "고등"] | None = None,
    _: str = Depends(require_researcher),
) -> list[ChatSubmissionPreview]:
    participants: ParticipantAccountRepository | None = getattr(
        request.app.state, "participant_account_repository", None
    )
    if participants is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="참여자 정보를 준비 중입니다.")
    profiles = {
        participant.participant_id: (participant.phone, participant.school_level, participant.grade)
        for participant in await participants.list_participants()
    }
    submissions = await _chat_submission_repository(request).list_submissions(submission_point)
    rows: list[ChatSubmissionPreview] = []
    for submission in reversed(submissions):
        profile = profiles.get(submission.participant_id, ("-", None, None))
        if school_level is not None and profile[1] != school_level:
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
                filenames=[attachment.filename for attachment in submission.attachments],
                attachmentCount=len(submission.attachments),
                submittedAt=submission.submitted_at,
            )
        )
    return rows[:100]


@router.get("/chat-submissions/files/{submission_id}/download")
async def download_chat_submission_files(
    submission_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    submissions = await _chat_submission_repository(request).list_submissions()
    submission = next((item for item in submissions if item.submission_id == submission_id), None)
    if submission is None or not submission.attachments:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="다운로드할 제출 파일을 찾을 수 없습니다.")
    uploads: ChatUploadRepository | None = getattr(request.app.state, "chat_upload_repository", None)
    if uploads is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="파일 다운로드 서비스를 준비 중입니다."
        )
    fd, temp_name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    archive_path = Path(temp_name)
    try:
        await build_chat_archive([submission], uploads, archive_path, include_deletion_requested=True)
        content = archive_path.read_bytes()
    finally:
        archive_path.unlink(missing_ok=True)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="chat-submission-{submission_id}.zip"'},
    )


@router.post(
    "/chat-submissions/download-jobs", response_model=ChatDownloadJobAccepted, status_code=status.HTTP_202_ACCEPTED
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
            },
        )
    )
    return ChatDownloadJobAccepted(jobId=job.id, status=job.status)


@router.get("/chat-submissions/download-jobs/{job_id}", response_model=ChatDownloadJobStatus)
async def get_chat_download_job(
    job_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatDownloadJobStatus:
    job = await _job_repository(request).get(job_id)
    if job is None or job.job_type != "chat_download":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="다운로드 작업을 찾을 수 없습니다.")
    artifact = (
        await _chat_download_artifact_repository(request).get(job.idempotency_key)
        if job.status == "completed"
        else None
    )
    return ChatDownloadJobStatus(
        jobId=job.id,
        status=job.status,
        error=job.error,
        downloadUrl=f"/api/v1/researcher/chat-submissions/download-jobs/{job.id}/file" if artifact else None,
    )


@router.get("/chat-submissions/download-jobs/{job_id}/file", name="download_chat_job_file")
async def download_chat_job_file(
    job_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> Response:
    job = await _job_repository(request).get(job_id)
    if job is None or job.job_type != "chat_download":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="다운로드 작업을 찾을 수 없습니다.")
    artifact = await _chat_download_artifact_repository(request).get(job.idempotency_key)
    uploads: ChatUploadRepository | None = getattr(request.app.state, "chat_download_upload_repository", None)
    if artifact is None or uploads is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="다운로드 파일이 아직 준비되지 않았습니다.")
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
    submissions = await _chat_submission_repository(request).list_submissions(status=status_filter)
    return [
        ChatSubmissionSummary.model_validate(submission_summary(submission, include_participant=True))
        for submission in reversed(submissions)
    ]


@router.delete("/chat-submissions/files/{submission_id}", response_model=ChatSubmissionDeleted)
async def delete_requested_chat_submission(
    submission_id: str,
    request: Request,
    _: str = Depends(require_researcher),
) -> ChatSubmissionDeleted:
    if not await _chat_submission_management_service(request).delete_requested_submission(submission_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="삭제 요청된 파일 제출 내역을 찾을 수 없습니다."
        )
    return ChatSubmissionDeleted(status="deleted")
