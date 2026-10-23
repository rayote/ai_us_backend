from __future__ import annotations

from app.api.auth import _participant_id
from app.schemas.survey import (
    ParticipantSurveyProgress,
    SurveyProgressRecord,
    SurveyProgressSave,
    SurveySessionHeartbeat,
    SurveySubmissionAccepted,
    SurveySubmissionCreate,
)
from app.services.auth import ParticipantAccountRepository
from app.services.jobs import JobRepository
from app.services.submissions import (
    SurveySubmissionService,
    UnknownQuestionKeyError,
    UnknownSurveyDefinitionError,
    _merge_participant_profile_answers,
)
from app.services.survey_progress import ProgressConflictError
from app.services.surveys import SurveyDefinitionRepository
from fastapi import APIRouter, Depends, HTTPException, Request, status

router = APIRouter(prefix="/api/v1", tags=["survey submissions"])


def _job_repository(request: Request) -> JobRepository:
    repository = getattr(request.app.state, "job_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="제출 서비스를 준비 중입니다.",
        )
    return repository


def _submission_service(request: Request) -> SurveySubmissionService:
    definitions: SurveyDefinitionRepository | None = getattr(
        request.app.state, "survey_definition_repository", None
    )
    if definitions is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="설문 정의 서비스를 준비 중입니다.",
        )
    return SurveySubmissionService(definitions, _job_repository(request))


def _participant_repository(request: Request) -> ParticipantAccountRepository | None:
    return getattr(request.app.state, "participant_account_repository", None)


def _session_repository(request: Request):
    repository = getattr(request.app.state, "survey_session_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="설문 세션 서비스를 준비 중입니다.",
        )
    return repository


async def _require_survey_access(
    request: Request, participant_id: str, survey_round: int, survey_version: str
):
    if survey_round == 1 and not request.app.state.settings.t1_submissions_open:
        raise HTTPException(status_code=403, detail="참여 기간이 끝났어요")
    definitions = getattr(request.app.state, "survey_definition_repository", None)
    responses = getattr(request.app.state, "survey_response_repository", None)
    if definitions is None:
        raise HTTPException(status_code=503, detail="설문 정의 서비스를 준비 중입니다.")
    definition = await definitions.get(survey_round, survey_version)
    if definition is None:
        raise HTTPException(status_code=404, detail="설문 정의를 찾을 수 없습니다.")
    if survey_round == 2 and (definition.spec or {}).get("part") == 2:
        if responses is None:
            raise HTTPException(
                status_code=503, detail="설문 응답 서비스를 준비 중입니다."
            )
        completed = await responses.list_versions_for_participant(
            participant_id, survey_round
        )
        for version in completed:
            previous = await definitions.get(survey_round, version)
            if previous is not None and (previous.spec or {}).get("part") == 1:
                break
        else:
            raise HTTPException(
                status_code=403, detail="파트 1 제출 완료 후 참여할 수 있습니다."
            )
    return definition


async def _survey_status(request: Request, participant_id: str) -> dict[str, object]:
    definitions = getattr(request.app.state, "survey_definition_repository", None)
    responses = getattr(request.app.state, "survey_response_repository", None)
    if definitions is None or responses is None:
        raise HTTPException(
            status_code=503, detail="설문 진행 조회 서비스를 준비 중입니다."
        )
    versions_by_round = {}
    completed_rounds = []
    for survey_round in range(1, 5):
        versions = sorted(
            set(
                await responses.list_versions_for_participant(
                    participant_id, survey_round
                )
            )
        )
        versions_by_round[str(survey_round)] = versions
        for version in versions:
            definition = await definitions.get(survey_round, version)
            if definition is not None and (definition.spec or {}).get("part") == 2:
                completed_rounds.append(survey_round)
                break
    return {
        "completedVersionsByRound": versions_by_round,
        "completedRounds": completed_rounds,
        "t1SubmissionsOpen": request.app.state.settings.t1_submissions_open,
    }


@router.get("/participant/survey-status")
async def participant_survey_status(
    request: Request, participant_id: str = Depends(_participant_id)
):
    return await _survey_status(request, participant_id)


@router.post("/participant/survey-sessions/heartbeat", status_code=status.HTTP_200_OK)
async def survey_session_heartbeat(
    heartbeat: SurveySessionHeartbeat,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> None:
    await _session_repository(request).heartbeat(participant_id, heartbeat)


@router.post(
    "/survey-responses",
    response_model=SurveySubmissionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_survey_response(
    submission: SurveySubmissionCreate,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> SurveySubmissionAccepted:
    await _require_survey_access(
        request, participant_id, submission.survey_round, submission.survey_version
    )
    try:
        participants = _participant_repository(request)
        participant = (
            await participants.find_by_id(participant_id)
            if participants is not None
            else None
        )
        job = await _submission_service(request).submit(
            participant_id, submission, participant
        )
    except UnknownSurveyDefinitionError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="설문 정의를 찾을 수 없습니다.",
        ) from error
    except UnknownQuestionKeyError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"알 수 없는 문항 키: {error}",
        ) from error
    return SurveySubmissionAccepted(submissionId=job.id, status=job.status)


@router.get("/participant/survey-progress", response_model=ParticipantSurveyProgress)
async def participant_survey_progress(
    survey_round: int,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> ParticipantSurveyProgress:
    responses = getattr(request.app.state, "survey_response_repository", None)
    if responses is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="설문 응답 서비스를 준비 중입니다.",
        )
    completed_versions = sorted(
        await responses.list_versions_for_participant(participant_id, survey_round)
    )
    return ParticipantSurveyProgress(
        surveyRound=survey_round, completedVersions=completed_versions
    )


def _progress_repository(request: Request):
    repository = getattr(request.app.state, "survey_progress_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="진행 저장 서비스를 준비 중입니다.")
    return repository


@router.get("/survey-progress", response_model=SurveyProgressRecord)
async def get_survey_progress(
    surveyRound: int,
    surveyVersion: str,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> SurveyProgressRecord:
    progress = await _progress_repository(request).get(
        participant_id, surveyRound, surveyVersion
    )
    if progress is None:
        raise HTTPException(status_code=404, detail="저장된 진행 상태가 없습니다.")
    return progress


@router.put("/survey-progress", response_model=SurveyProgressRecord)
async def save_survey_progress(
    progress: SurveyProgressSave,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> SurveyProgressRecord:
    await _require_survey_access(
        request, participant_id, progress.survey_round, progress.survey_version
    )
    definitions = getattr(request.app.state, "survey_definition_repository", None)
    if definitions is None:
        raise HTTPException(status_code=503, detail="설문 정의 서비스를 준비 중입니다.")
    definition = await definitions.get(progress.survey_round, progress.survey_version)
    if definition is None:
        raise HTTPException(status_code=404, detail="설문 정의를 찾을 수 없습니다.")
    responses = getattr(request.app.state, "survey_response_repository", None)
    if responses is None:
        raise HTTPException(status_code=503, detail="설문 응답 서비스를 준비 중입니다.")
    if progress.survey_version in await responses.list_versions_for_participant(
        participant_id, progress.survey_round
    ):
        raise HTTPException(status_code=409, detail="이미 제출 완료된 설문입니다.")
    participants = _participant_repository(request)
    participant = (
        await participants.find_by_id(participant_id)
        if participants is not None
        else None
    )
    if participant is None:
        raise HTTPException(status_code=401, detail="참여자 정보를 확인할 수 없습니다.")
    known_keys = {question.key for question in definition.questions}
    if set(progress.answers) - known_keys:
        raise HTTPException(status_code=422, detail="알 수 없는 문항 키가 있습니다.")
    progress = progress.model_copy(
        update={
            "answers": _merge_participant_profile_answers(
                progress.answers,
                known_keys,
                participant,
                reenter=progress.survey_round == 2,
            ),
            "detail": {
                **progress.detail,
                "demographicEntryPolicy": (
                    "reenter" if progress.survey_round == 2 else "profile"
                ),
                "demographicPolicyVersion": "2026-10-23",
            },
        }
    )
    try:
        return await _progress_repository(request).save(participant_id, progress)
    except ProgressConflictError as error:
        raise HTTPException(
            status_code=409, detail="다른 창이나 기기에서 진행 상태가 변경되었습니다."
        ) from error


@router.get("/submission-jobs/{submission_id}", response_model=SurveySubmissionAccepted)
async def get_submission_status(
    submission_id: str,
    request: Request,
    participant_id: str = Depends(_participant_id),
) -> SurveySubmissionAccepted:
    job = await _job_repository(request).get(submission_id)
    if (
        job is None
        or job.job_type not in {"survey_response", "chat_submission"}
        or job.payload.get("participantId") != participant_id
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출 작업을 찾을 수 없습니다.",
        )
    return SurveySubmissionAccepted(submissionId=job.id, status=job.status)
