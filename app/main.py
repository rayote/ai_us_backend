from __future__ import annotations

from contextlib import asynccontextmanager

from app.api.applications import router as applications_router
from app.api.auth import router as auth_router
from app.api.researcher import router as researcher_router
from app.core.settings import Settings
from app.db.mongodb import MongoDatabase
from app.services.applications import ApplicationRepository, MongoApplicationRepository
from app.services.auth import (
    MongoParticipantAccountRepository,
    MongoResearcherAccountRepository,
    ParticipantAccountRepository,
    ResearcherAccountRepository,
)
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware


def create_app(
    settings: Settings | None = None,
    application_repository: ApplicationRepository | None = None,
    participant_account_repository: ParticipantAccountRepository | None = None,
    researcher_account_repository: ResearcherAccountRepository | None = None,
) -> FastAPI:
    application_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database: MongoDatabase | None = None
        app.state.settings = application_settings
        if application_repository is not None:
            app.state.application_repository = application_repository
        elif application_settings.mongodb_uri:
            database = MongoDatabase(
                application_settings.mongodb_uri,
                application_settings.database_name,
            )
            await database.connect()
            app.state.application_repository = MongoApplicationRepository(database.database["applications"])
        if participant_account_repository is not None:
            app.state.participant_account_repository = participant_account_repository
        elif application_settings.mongodb_uri:
            app.state.participant_account_repository = MongoParticipantAccountRepository(
                database.database["participants"]
            )
        if researcher_account_repository is not None:
            app.state.researcher_account_repository = researcher_account_repository
        elif application_settings.mongodb_uri:
            repository = MongoResearcherAccountRepository(database.database["researchers"])
            if (
                application_settings.researcher_bootstrap_username
                and application_settings.researcher_bootstrap_password
            ):
                from app.core.security import hash_password

                await repository.ensure_bootstrap(
                    application_settings.researcher_bootstrap_username,
                    hash_password(application_settings.researcher_bootstrap_password),
                )
            app.state.researcher_account_repository = repository

        yield

        if database is not None:
            await database.close()

    app = FastAPI(title="AI 마음 탐사대 API", version="0.1.0", lifespan=lifespan)

    if application_settings.frontend_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(application_settings.frontend_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.get("/health", status_code=status.HTTP_200_OK)
    async def health_check() -> dict[str, str]:
        return {"status": "ok", "environment": application_settings.environment}

    app.include_router(applications_router)
    app.include_router(auth_router)
    app.include_router(researcher_router)

    return app


app = create_app()
