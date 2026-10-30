from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

from app.core.settings import Settings
from app.db.mongodb import MongoDatabase
from app.services.abuse_review import AbuseReviewService
from app.services.analysis_jobs import ANALYSIS_HEARTBEAT_ID, MongoAnalysisJobRepository
from app.services.auth import MongoParticipantAccountRepository
from app.services.jobs import QueueWorker
from app.services.surveys import (
    MongoSurveyDefinitionRepository,
    MongoSurveyResponseRepository,
)
from app.services.worker_health import MongoWorkerHealthRepository
from app.worker import acquire_worker_process_lock, maintain_worker_heartbeat

ANALYSIS_PROCESS_LOCK_PATH = "/tmp/ai-us-analysis-worker.lock"


async def calculate_report(
    service: AbuseReviewService,
    repository: MongoAnalysisJobRepository,
    payload: dict[str, Any],
) -> None:
    report = await asyncio.to_thread(
        lambda: asyncio.run(
            service.report(
                payload.get("surveyRound"),
                payload.get("surveyVersion"),
                include_all_candidates=True,
            )
        )
    )
    await repository.store_report(str(payload["jobId"]), report)


async def run_worker() -> None:
    process_lock = acquire_worker_process_lock(ANALYSIS_PROCESS_LOCK_PATH)
    settings = Settings.from_environment()
    if settings.mongodb_uri is None:
        process_lock.close()
        raise RuntimeError(
            "MONGODB_URI must be configured before starting the analysis worker"
        )
    try:
        os.nice(10)
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not lower analysis worker CPU priority"
        )
    database = MongoDatabase(settings.mongodb_uri, settings.database_name)
    heartbeat_task = None
    try:
        await database.connect()
        repository = MongoAnalysisJobRepository(database.database["analysis_jobs"])
        service = AbuseReviewService(
            MongoParticipantAccountRepository(database.database["participants"]),
            MongoSurveyResponseRepository(database.database["survey_responses"]),
            MongoSurveyDefinitionRepository(database.database["survey_definitions"]),
        )
        worker = QueueWorker(
            repository,
            {
                "abuse_review": lambda payload: calculate_report(
                    service, repository, payload
                )
            },
            max_attempts=1,
        )
        await worker.recover()
        health = MongoWorkerHealthRepository(
            database.database["service_heartbeats"], ANALYSIS_HEARTBEAT_ID
        )
        heartbeat_task = asyncio.create_task(maintain_worker_heartbeat(health))
        while True:
            if not await worker.process_one():
                await asyncio.sleep(0.5)
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        await database.close()
        process_lock.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
