from __future__ import annotations

import argparse
import getpass
import os
from urllib.parse import quote

from pymongo import MongoClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill participant email fields from approved applications.")
    parser.add_argument("--mongodb-uri")
    parser.add_argument("--database-name", default=os.getenv("DATABASE_NAME", "ai_us_development"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = MongoClient(args.mongodb_uri or os.getenv("MONGODB_URI") or prompt_mongodb_uri())
    database = client[args.database_name]
    participants = database["participants"]
    applications = database["applications"]

    updates = []
    for participant in participants.find({"role": "participant", "email": {"$exists": False}}):
        application = applications.find_one(
            {"phone_normalized": participant["phone_normalized"], "email": {"$exists": True}},
            sort=[("approved_at", -1), ("submitted_at", -1)],
        )
        if application and application.get("email"):
            updates.append((participant["_id"], participant["phone_normalized"], application["email"]))

    for participant_id, phone, email in updates:
        print(f"{phone}: {email}")
        if not args.dry_run:
            participants.update_one({"_id": participant_id}, {"$set": {"email": email}})
    print(f"{'Would update' if args.dry_run else 'Updated'} {len(updates)} participant(s).")
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
