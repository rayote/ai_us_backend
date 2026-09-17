from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

VERSIONS = ("t1-elem-part2-v2-0916", "t1-secondary-part2-v2-0916")
ANSWER_KEY = "followup.researchConsent"
DEFAULT_VALUE = 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill the follow-up research consent answer.")
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.apply and args.confirm != "BACKFILL-FOLLOWUP-CONSENT":
        parser.error("--apply requires --confirm BACKFILL-FOLLOWUP-CONSENT")

    from app.core.settings import Settings

    settings = Settings.from_environment()
    uri = args.mongodb_uri or settings.mongodb_uri
    database_name = args.database_name or settings.database_name
    if uri is None:
        parser.error("Set MONGODB_URI or MONGODB_HOST/MONGODB_USERNAME/MONGODB_PASSWORD")

    client = MongoClient(uri, tz_aware=True)
    collection = client[database_name].survey_responses
    try:
        filters = {"survey_version": {"$in": list(VERSIONS)}, f"answers.{ANSWER_KEY}": {"$exists": False}}
        count = collection.count_documents(filters)
        print(
            {
                "mode": "apply" if args.apply else "dry-run",
                "database": database_name,
                "versions": list(VERSIONS),
                "answer_key": ANSWER_KEY,
                "default_value": DEFAULT_VALUE,
                "records_to_update": count,
            }
        )
        if args.apply:
            result = collection.update_many(filters, {"$set": {f"answers.{ANSWER_KEY}": DEFAULT_VALUE}})
            print({"matched": result.matched_count, "modified": result.modified_count})
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
