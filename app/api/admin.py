from __future__ import annotations

from app.api.auth import require_admin
from app.schemas.auth import ResearcherCreate, ResearcherCreated
from app.schemas.survey import SurveyDefinition, SurveyDefinitionCreate
from app.services.auth import ResearcherAccountRepository, ResearcherAdministrationService
from app.services.surveys import DuplicateSurveyDefinitionError, SurveyDefinitionRepository
from fastapi import APIRouter, Depends, HTTPException, Request, status

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _service(request: Request) -> ResearcherAdministrationService:
    repository: ResearcherAccountRepository | None = getattr(request.app.state, "researcher_account_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="계정 관리 서비스를 준비 중입니다."
        )
    return ResearcherAdministrationService(repository)


def _survey_definition_repository(request: Request) -> SurveyDefinitionRepository:
    repository = getattr(request.app.state, "survey_definition_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="설문 정의 서비스를 준비 중입니다."
        )
    return repository


@router.post("/researchers", response_model=ResearcherCreated, status_code=status.HTTP_201_CREATED)
async def create_researcher(
    researcher: ResearcherCreate,
    request: Request,
    _: str = Depends(require_admin),
) -> ResearcherCreated:
    researcher_id = await _service(request).create_researcher(researcher.username, researcher.password)
    if researcher_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 연구자 아이디입니다.")
    return ResearcherCreated(researcherId=researcher_id, username=researcher.username, role="researcher")


@router.post("/survey-definitions", response_model=SurveyDefinition, status_code=status.HTTP_201_CREATED)
async def create_survey_definition(
    definition: SurveyDefinitionCreate,
    request: Request,
    _: str = Depends(require_admin),
) -> SurveyDefinition:
    try:
        return await _survey_definition_repository(request).create(definition)
    except DuplicateSurveyDefinitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="이미 등록된 설문 회차와 버전입니다."
        ) from error
