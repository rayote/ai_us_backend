from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bson import ObjectId
from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from app.core.settings import Settings

    parser = argparse.ArgumentParser(
        description="Backfill status and tool metadata for existing chat file submissions."
    )
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_environment()
    mongodb_uri = args.mongodb_uri or settings.mongodb_uri
    if mongodb_uri is None:
        parser.error("MongoDB connection settings are required.")
    database = MongoClient(mongodb_uri)[args.database_name or settings.database_name]
    updated = 0
    for submission in database["chat_submissions"].find({}):
        updates: dict[str, object] = {}
        if "status" not in submission:
            updates["status"] = "active"
        if "tool" not in submission:
            tools = {
                grid_file.get("metadata", {}).get("tool")
                for attachment in submission.get("attachments", [])
                if (grid_file := database["chat_uploads.files"].find_one({"_id": ObjectId(attachment["fileId"])}))
            }
            tools.discard(None)
            if len(tools) == 1:
                updates["tool"] = tools.pop()
        if not updates:
            continue
        print(f"{submission['_id']}: {updates}")
        if not args.dry_run:
            database["chat_submissions"].update_one({"_id": submission["_id"]}, {"$set": updates})
        updated += 1
    print(f"{'Would update' if args.dry_run else 'Updated'} {updated} submission(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
