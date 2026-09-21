from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pymongo import MongoClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def frontend_metadata(frontend_index: Path) -> dict[str, dict[str, object]]:
    from register_frontend_surveys import extract_survey_sets

    metadata: dict[str, dict[str, object]] = {}
    for audience, parts in extract_survey_sets(frontend_index).items():
        for part in parts:
            version = str(part["surveyVersion"])
            round_number = part.get("surveyRound")
            part_number = part.get("part")
            audience_label = {"elementary": "초등", "secondary": "중고등"}.get(audience, audience)
            title = f"청소년 생성형 AI 사용 경험 연구 · {round_number}회차 파트{part_number} ({audience_label})"
            metadata[version] = {"title": title, "part": part.get("part"), "audience": audience}
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize survey definition display metadata from frontend SURVEY_SETS.")
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name")
    parser.add_argument("--frontend-index", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.core.settings import Settings
    from register_frontend_surveys import DEFAULT_FRONTEND_INDEX

    settings = Settings.from_environment()
    mongodb_uri = args.mongodb_uri or settings.mongodb_uri
    if mongodb_uri is None:
        parser.error("MONGODB_URI 또는 MONGODB_HOST/MONGODB_USERNAME/MONGODB_PASSWORD를 설정하세요.")
    collection = MongoClient(mongodb_uri)[args.database_name or settings.database_name]["survey_definitions"]
    metadata_by_version = frontend_metadata(args.frontend_index or DEFAULT_FRONTEND_INDEX)

    success = True
    for survey_version, metadata in metadata_by_version.items():
        document = collection.find_one({"survey_version": survey_version})
        if document is None:
            print(f"missing: {survey_version}")
            success = False
            continue
        spec = document.get("spec") or {}
        current_title = spec.get("_meta", {}).get("title") if isinstance(spec.get("_meta"), dict) else None
        current_part = spec.get("part")
        current_audience = document.get("audience")
        updates = {
            "spec._meta.title": metadata["title"],
            "spec.part": metadata["part"],
            "audience": metadata["audience"],
        }
        changed = (current_title, current_part, current_audience) != (
            metadata["title"],
            metadata["part"],
            metadata["audience"],
        )
        print(f"{survey_version}: {'update' if changed else 'ok'} · {metadata['title']}")
        if not args.dry_run:
            collection.update_one({"_id": document["_id"]}, {"$set": updates})

    collection.database.client.close()
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
