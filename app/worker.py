from __future__ import annotations

import asyncio

from app.core.settings import Settings
from app.db.mongodb import MongoDatabase
from app.services.chats import MongoChatSubmissionRepository, store_chat_submission
from app.services.jobs import MongoJobRepository, QueueWorker
from app.services.submissions import store_survey_response
from app.services.surveys import MongoSurveyDefinitionRepository, MongoSurveyResponseRepository


async def run_worker() -> None:
    settings = Settings.from_environment()
    if settings.mongodb_uri is None:
        raise RuntimeError("MONGODB_URI must be configured before starting the worker")

    database = MongoDatabase(settings.mongodb_uri, settings.database_name)
    await database.connect()
    definitions = MongoSurveyDefinitionRepository(database.database["survey_definitions"])
    responses = MongoSurveyResponseRepository(database.database["survey_responses"])
    chat_submissions = MongoChatSubmissionRepository(database.database["chat_submissions"])
    worker = QueueWorker(
        MongoJobRepository(database.database["submission_jobs"]),
        {
            "survey_response": lambda payload: store_survey_response(payload, definitions, responses),
            "chat_submission": lambda payload: store_chat_submission(payload, chat_submissions),
        },
    )
    await worker.recover()
    try:
        while True:
            processed = await worker.process_one()
            if not processed:
                await asyncio.sleep(0.5)
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
