from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    from app.core.settings import Settings
    from app.schemas.survey import SurveyDefinitionCreate
    from register_frontend_surveys import DEFAULT_FRONTEND_INDEX, build_payloads, extract_survey_sets

    parser = argparse.ArgumentParser(
        description="Replace existing MongoDB survey definitions from frontend SURVEY_SETS."
    )
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--frontend-index", type=Path, default=DEFAULT_FRONTEND_INDEX)
    parser.add_argument("--survey-round", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--upsert-missing",
        action="store_true",
        help="Insert missing definitions as well as updating existing ones. Intended for an empty development database.",
    )
    args = parser.parse_args()

    payloads = build_payloads(extract_survey_sets(args.frontend_index), args.survey_round)
    definitions = [SurveyDefinitionCreate.model_validate(payload) for payload in payloads]

    if args.dry_run:
        for definition in definitions:
            print(
                f"{definition.audience} {definition.survey_version}: would replace ({len(definition.questions)} answer fields)"
            )
        return 0

    settings = Settings.from_environment()
    mongodb_uri = args.mongodb_uri or settings.mongodb_uri
    if mongodb_uri is None:
        parser.error("MONGODB_URI 또는 MONGODB_HOST/MONGODB_USERNAME/MONGODB_PASSWORD를 .env에 설정하세요.")
    client = MongoClient(mongodb_uri)
    collection = client[args.database_name or settings.database_name]["survey_definitions"]
    success = True
    try:
        for definition in definitions:
            document = {
                "audience": definition.audience,
                "questions": [question.model_dump(by_alias=True) for question in definition.questions],
                "spec": definition.raw_spec,
            }
            query = {"survey_round": definition.survey_round, "survey_version": definition.survey_version}
            existing = collection.find_one(query, {"created_at": 1})
            if existing is None or "created_at" not in existing:
                document["created_at"] = datetime.now(UTC)
            result = collection.update_one(query, {"$set": document}, upsert=args.upsert_missing)
            status = "replaced" if result.matched_count == 1 else ("inserted" if result.upserted_id else "missing")
            print(
                f"{definition.audience} {definition.survey_version}: {status} "
                f"({len(definition.questions)} answer fields)"
            )
            success = success and (result.matched_count == 1 or result.upserted_id is not None)
    finally:
        client.close()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
