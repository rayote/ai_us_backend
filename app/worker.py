from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from app.core.settings import Settings
from app.db.mongodb import MongoDatabase
from app.services.auth import MongoParticipantAccountRepository
from app.services.chat_downloads import (
    MongoChatDownloadArtifactRepository,
    chat_download_filename,
    new_artifact,
    transcript_download_filename,
)
from app.services.chats import MongoChatSubmissionRepository, build_chat_archive, store_chat_submission
from app.services.jobs import MongoJobRepository, QueueWorker
from app.services.submissions import store_survey_response
from app.services.surveys import MongoSurveyDefinitionRepository, MongoSurveyResponseRepository
from app.services.transcript_runs import (
    MongoTranscriptParseRunRepository,
    build_transcript_archive,
    process_transcript_parse,
)


async def run_worker() -> None:
    settings = Settings.from_environment()
    if settings.mongodb_uri is None:
        raise RuntimeError("MONGODB_URI must be configured before starting the worker")

    database = MongoDatabase(settings.mongodb_uri, settings.database_name)
    await database.connect()
    definitions = MongoSurveyDefinitionRepository(database.database["survey_definitions"])
    responses = MongoSurveyResponseRepository(database.database["survey_responses"])
    chat_submissions = MongoChatSubmissionRepository(database.database["chat_submissions"])
    chat_download_artifacts = MongoChatDownloadArtifactRepository(database.database["chat_download_artifacts"])
    participants = MongoParticipantAccountRepository(database.database["participants"])
    transcript_runs = MongoTranscriptParseRunRepository(database.database["transcript_parse_runs"])

    async def selected_submissions(payload: dict[str, object]) -> list:
        submissions = await chat_submissions.list_submissions(
            str(payload.get("submissionPoint")) if payload.get("submissionPoint") else None
        )
        selected_ids = {str(value) for value in payload.get("submissionIds", []) if isinstance(value, str)}
        if selected_ids:
            submissions = [submission for submission in submissions if submission.submission_id in selected_ids]
        school_level = payload.get("schoolLevel")
        if isinstance(school_level, str):
            profiles = {
                participant.participant_id: participant.school_level
                for participant in await participants.list_participants()
            }
            submissions = [
                submission for submission in submissions if profiles.get(submission.participant_id) == school_level
            ]
        return submissions

    async def store_download_artifact(payload: dict[str, object], archive_path: Path, filename: str) -> None:
        file_id = await database.gridfs_bucket("chat_downloads").upload_file(
            filename, archive_path, {"job_key": payload["jobKey"]}
        )
        await chat_download_artifacts.create(
            new_artifact(str(payload["jobKey"]), file_id, filename, archive_path.stat().st_size)
        )

    async def build_chat_download(payload: dict[str, object]) -> None:
        submissions = await selected_submissions(payload)
        fd, archive_name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        archive_path = Path(archive_name)
        try:
            count = await build_chat_archive(submissions, database.gridfs_bucket("chat_uploads"), archive_path)
            if count == 0:
                raise ValueError("선택한 제출에 다운로드할 첨부 파일이 없습니다.")
            await store_download_artifact(payload, archive_path, chat_download_filename())
        finally:
            archive_path.unlink(missing_ok=True)

    async def build_transcript_download(payload: dict[str, object]) -> None:
        submissions = await selected_submissions(payload)
        if not submissions:
            raise ValueError("선택한 제출이 없습니다.")
        participant_profiles = {
            participant.participant_id: (participant.phone, participant.school_level, participant.grade)
            for participant in await participants.list_participants()
        }
        fd, archive_name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        archive_path = Path(archive_name)
        try:
            await build_transcript_archive(
                submissions,
                chat_submissions,
                database.gridfs_bucket("chat_uploads"),
                transcript_runs,
                archive_path,
                participant_profiles,
            )
            await store_download_artifact(payload, archive_path, transcript_download_filename())
        finally:
            archive_path.unlink(missing_ok=True)

    worker = QueueWorker(
        MongoJobRepository(database.database["submission_jobs"]),
        {
            "survey_response": lambda payload: store_survey_response(payload, definitions, responses),
            "chat_submission": lambda payload: store_chat_submission(payload, chat_submissions),
            "chat_download": build_chat_download,
            "transcript_download": build_transcript_download,
            "transcript_parse": lambda payload: process_transcript_parse(
                payload, chat_submissions, database.gridfs_bucket("chat_uploads"), transcript_runs
            ),
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
