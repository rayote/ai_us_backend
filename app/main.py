from __future__ import annotations

from contextlib import asynccontextmanager

from app.api.applications import router as applications_router
from app.core.settings import Settings
from app.db.mongodb import MongoDatabase
from app.services.applications import ApplicationRepository, MongoApplicationRepository
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware


def create_app(
    settings: Settings | None = None,
    application_repository: ApplicationRepository | None = None,
) -> FastAPI:
    application_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database: MongoDatabase | None = None
        if application_repository is not None:
            app.state.application_repository = application_repository
        elif application_settings.mongodb_uri:
            database = MongoDatabase(
                application_settings.mongodb_uri,
                application_settings.database_name,
            )
            await database.connect()
            app.state.application_repository = MongoApplicationRepository(database.database["applications"])

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

    return app


app = create_app()
