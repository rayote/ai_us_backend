from __future__ import annotations

import argparse
import getpass
import os
from urllib.parse import quote

from pymongo import MongoClient

TITLE_UPDATES = {
    "demo-v1": {"title": "개발용 더미 설문 · demo-v1", "part": None},
    "t1-elem-part1-v1-draft": {"title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (초등)", "part": 1},
    "t1-elem-part2-v1-draft": {"title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트2 (초등)", "part": 2},
    "t1-secondary-part1-v1-draft": {"title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트1 (중고등)", "part": 1},
    "t1-secondary-part2-v1-draft": {"title": "청소년 생성형 AI 사용 경험 연구 · 1회차 파트2 (중고등)", "part": 2},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Patch survey definition display titles in MongoDB.")
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name", default=os.getenv("DATABASE_NAME", "ai_us_development"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = MongoClient(args.mongodb_uri or os.getenv("MONGODB_URI") or prompt_mongodb_uri())
    collection = client[args.database_name]["survey_definitions"]

    for survey_version, metadata in TITLE_UPDATES.items():
        document = collection.find_one({"survey_version": survey_version})
        if document is None:
            print(f"missing: {survey_version}")
            continue
        updates = {"spec._meta.title": metadata["title"]}
        if metadata["part"] is not None:
            updates["spec.part"] = metadata["part"]
        print(f"{survey_version}: {metadata['title']}")
        if not args.dry_run:
            collection.update_one({"_id": document["_id"]}, {"$set": updates})

    client.close()
    return 0


def prompt_mongodb_uri() -> str:
    host = input("MongoDB host: ").strip()
    port = input("MongoDB port: ").strip()
    username = input("MongoDB username: ").strip()
    password = getpass.getpass("MongoDB password: ")
    return f"mongodb://{quote(username)}:{quote(password)}@{host}:{port}/?authSource=admin"


if __name__ == "__main__":
    raise SystemExit(main())
