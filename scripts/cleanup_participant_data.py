from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report or remove non-beta participant data. Dry-run is the default."
    )
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--cutoff-kst", default="2026-09-17 11:00")
    parser.add_argument(
        "--keep-phone",
        action="append",
        dest="keep_phones",
        default=["01088973147", "01030947405", "01076354722"],
    )
    parser.add_argument("--apply", action="store_true", help="Actually delete the reported records.")
    parser.add_argument(
        "--confirm",
        help="Required with --apply: type DELETE-NON-BETA-DATA exactly.",
    )
    return parser.parse_args()


def parse_cutoff(value: str) -> datetime:
    local = datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return local.astimezone(UTC)


def ids_for_documents(documents: list[dict[str, Any]]) -> set[str]:
    return {str(document["_id"]) for document in documents}


def gridfs_ids(database: Any, bucket: str, excluded: set[str]) -> list[str]:
    return [str(document["_id"]) for document in database[f"{bucket}.files"].find({"_id": {"$nin": [ObjectId(value) for value in excluded if ObjectId.is_valid(value)]}}, {"_id": 1})]


def main() -> int:
    args = parse_args()
    if args.apply and args.confirm != "DELETE-NON-BETA-DATA":
        raise SystemExit("--apply requires --confirm DELETE-NON-BETA-DATA")

    from app.core.settings import Settings

    settings = Settings.from_environment()
    uri = args.mongodb_uri or settings.mongodb_uri
    database_name = args.database_name or settings.database_name
    if uri is None:
        raise SystemExit("Set MONGODB_URI or MONGODB_HOST/MONGODB_USERNAME/MONGODB_PASSWORD")

    cutoff_utc = parse_cutoff(args.cutoff_kst)
    keep_phones = {phone.replace("-", "") for phone in args.keep_phones}
    client = MongoClient(uri, tz_aware=True)
    database = client[database_name]

    try:
        kept_participants = list(database.participants.find({"phone_normalized": {"$in": sorted(keep_phones)}}))
        kept_participant_ids = ids_for_documents(kept_participants)
        kept_applications = list(database.applications.find({"phone_normalized": {"$in": sorted(keep_phones)}}))
        all_participants = list(database.participants.find({"role": "participant"}, {"_id": 1, "phone_normalized": 1}))
        all_applications = list(database.applications.find({}, {"_id": 1, "phone_normalized": 1, "submitted_at": 1}))

        response_filter = {"participant_id": {"$nin": sorted(kept_participant_ids)}}
        responses_to_remove = list(database.survey_responses.find(response_filter, {"_id": 1, "participant_id": 1, "survey_version": 1}))

        job_filter = {"payload.participantId": {"$nin": sorted(kept_participant_ids)}}
        jobs_to_remove = list(database.submission_jobs.find(job_filter, {"_id": 1, "job_type": 1, "status": 1, "payload.participantId": 1}))

        chat_filter = {"participant_id": {"$nin": sorted(kept_participant_ids)}}
        chats_to_remove = list(database.chat_submissions.find(chat_filter, {"_id": 1, "participant_id": 1, "attachments": 1}))
        kept_chats = list(database.chat_submissions.find({"participant_id": {"$in": sorted(kept_participant_ids)}}, {"attachments": 1}))
        kept_file_ids = {
            str(attachment.get("fileId"))
            for chat in kept_chats
            for attachment in chat.get("attachments", [])
            if attachment.get("fileId")
        }
        removable_upload_ids = gridfs_ids(database, "chat_uploads", kept_file_ids)

        all_download_jobs = list(database.submission_jobs.find({"job_type": "chat_download"}, {"_id": 1, "status": 1}))
        all_download_artifacts = list(database.chat_download_artifacts.find({}, {"_id": 1, "file_id": 1}))
        removable_download_file_ids = [str(artifact["file_id"]) for artifact in all_download_artifacts if artifact.get("file_id")]

        report = {
            "mode": "apply" if args.apply else "dry-run",
            "database": database_name,
            "cutoffKst": args.cutoff_kst,
            "cutoffUtc": cutoff_utc.isoformat(),
            "keepPhones": sorted(keep_phones),
            "keptParticipants": [{"id": str(d["_id"]), "phone": d.get("phone_normalized")} for d in kept_participants],
            "missingKeepPhones": sorted(keep_phones - {d.get("phone_normalized") for d in kept_participants}),
            "counts": {
                "participantsTotal": len(all_participants),
                "participantsToRemove": len(all_participants) - len(kept_participants),
                "applicationsTotal": len(all_applications),
                "applicationsToRemove": len(all_applications) - len(kept_applications),
                "applicationsAfterCutoff": sum(1 for d in all_applications if d.get("submitted_at") and d["submitted_at"] >= cutoff_utc),
                "surveyResponsesToRemove": len(responses_to_remove),
                "submissionJobsToRemove": len(jobs_to_remove),
                "chatSubmissionsToRemove": len(chats_to_remove),
                "chatUploadFilesToRemove": len(removable_upload_ids),
                "chatDownloadJobsToRemove": len(all_download_jobs),
                "chatDownloadArtifactsToRemove": len(all_download_artifacts),
                "chatDownloadFilesToRemove": len(removable_download_file_ids),
            },
        }
        print(report)

        if not args.apply:
            return 0

        database.participants.delete_many({"role": "participant", "_id": {"$nin": [d["_id"] for d in kept_participants]}})
        database.applications.delete_many({"_id": {"$nin": [d["_id"] for d in kept_applications]}})
        database.survey_responses.delete_many(response_filter)
        database.submission_jobs.delete_many(job_filter)
        database.chat_submissions.delete_many(chat_filter)
        database["chat_uploads.files"].delete_many({"_id": {"$in": [ObjectId(value) for value in removable_upload_ids]}})
        database["chat_uploads.chunks"].delete_many({"files_id": {"$in": [ObjectId(value) for value in removable_upload_ids]}})
        database.submission_jobs.delete_many({"job_type": "chat_download"})
        database.chat_download_artifacts.delete_many({})
        database["chat_downloads.files"].delete_many({"_id": {"$in": [ObjectId(value) for value in removable_download_file_ids]}})
        database["chat_downloads.chunks"].delete_many({"files_id": {"$in": [ObjectId(value) for value in removable_download_file_ids]}})
        print("Applied cleanup.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())