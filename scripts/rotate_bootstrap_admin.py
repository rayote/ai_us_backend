from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sys
import warnings

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.core.security import hash_password, verify_password


def sole_admin(collection, expected_username: str) -> dict:
    admins = list(collection.find({"role": "admin"}).limit(2))
    if len(admins) != 1 or admins[0].get("username") != expected_username:
        raise ValueError("Expected exactly one admin matching the current username.")
    return admins[0]


def validate_credentials(admin: dict, new_username: str, current_password: str, new_password: str) -> None:
    if not new_username or new_username != new_username.strip() or len(new_username) > 100:
        raise ValueError("Enter a username of 1 to 100 characters without surrounding spaces.")
    if not 16 <= len(new_password) <= 256:
        raise ValueError("Enter a new password of 16 to 256 characters.")
    if not verify_password(current_password, admin["password_hash"]):
        raise ValueError("The current password did not match.")
    if new_password == current_password:
        raise ValueError("The new password must differ from the current password.")


def add_admin(collection, admin: dict, new_username: str, current_password: str, new_password: str) -> None:
    validate_credentials(admin, new_username, current_password, new_password)
    if collection.find_one({"username": new_username}, {"_id": 1}):
        raise ValueError("The new username is already in use.")
    try:
        collection.insert_one({"username": new_username, "password_hash": hash_password(new_password), "role": "admin"})
    except DuplicateKeyError:
        raise ValueError("The new username is already in use.") from None


def rotate_admin(collection, admin: dict, new_username: str, current_password: str, new_password: str) -> None:
    validate_credentials(admin, new_username, current_password, new_password)
    if new_username != admin["username"] and collection.find_one({"username": new_username}, {"_id": 1}):
        raise ValueError("The new username is already in use.")
    try:
        result = collection.update_one(
            {key: admin[key] for key in ("_id", "role", "username", "password_hash")},
            {"$set": {"username": new_username, "password_hash": hash_password(new_password)}},
        )
    except DuplicateKeyError:
        raise ValueError("The new username is already in use.") from None
    if result.modified_count != 1:
        raise ValueError("The admin changed during rotation. No update was applied.")


def retire_admin(collection, current_username: str, successor_username: str, *, apply: bool) -> None:
    if not current_username or not successor_username or current_username == successor_username:
        raise ValueError("The current and successor usernames must be distinct and nonempty.")
    admin = collection.find_one({"username": current_username, "role": "admin"}, {"password_hash": 1})
    if not admin:
        raise ValueError("The current username does not match an admin.")
    settings = {"role": "researcher", "password_hash": hash_password(secrets.token_urlsafe(32))} if apply else None
    if not collection.find_one({"username": successor_username, "role": "admin"}, {"_id": 1}):
        raise ValueError("The successor username does not match an admin.")
    if not apply:
        return
    result = collection.update_one(
        {"_id": admin["_id"], "username": current_username, "role": "admin", "password_hash": admin["password_hash"]},
        {"$set": settings},
    )
    if result.modified_count != 1:
        raise ValueError("The current admin changed. No update was applied.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage application admins in MongoDB.")
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--current-username", required=True)
    parser.add_argument("--apply", action="store_true", help="Write the requested change instead of checking only.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--add-admin", action="store_true", help="Create a new admin without changing the existing one.")
    mode.add_argument("--retire-admin", metavar="SUCCESSOR_USERNAME", help="Disable the current admin login after verifying the named successor's login.")
    parser.add_argument("--bootstrap-disabled", action="store_true", help="Confirm that the CloudType bootstrap secrets were removed.")
    args = parser.parse_args()
    if args.apply and not args.add_admin and args.retire_admin is None and not args.bootstrap_disabled:
        parser.error("--apply requires --bootstrap-disabled after the bootstrap secrets have been removed.")
    if ((args.apply and args.retire_admin is None) or not os.getenv("MONGODB_URI")) and not sys.stdin.isatty():
        parser.error("An interactive terminal is required for secret prompts.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            uri = os.getenv("MONGODB_URI") or getpass.getpass("MongoDB URI: ")
            if not uri:
                raise ValueError("A MongoDB URI is required.")
            with MongoClient(uri, serverSelectionTimeoutMS=5000, socketTimeoutMS=8000) as client:
                collection = client[args.database_name].researchers
                if args.retire_admin is not None:
                    retire_admin(collection, args.current_username, args.retire_admin, apply=args.apply)
                    if args.apply:
                        print("Previous admin demoted and password replaced. Username unchanged.")
                    else:
                        print("Current and successor admins matched. No changes were made.")
                    return 0
                admin = sole_admin(collection, args.current_username)
                if not args.apply:
                    print("Exactly one admin matched. No changes were made.")
                    return 0
                current_password = getpass.getpass("Current admin password: ")
                new_username = input("New admin username: ").strip()
                new_password = getpass.getpass("New admin password: ")
                if new_password != getpass.getpass("Confirm new admin password: "):
                    raise ValueError("New passwords do not match.")
                if args.add_admin:
                    add_admin(collection, admin, new_username, current_password, new_password)
                    print("New admin added. The existing admin was not changed. Verify the new login in a fresh session.")
                else:
                    rotate_admin(collection, admin, new_username, current_password, new_password)
                    print("Admin credentials updated. Verify the new login in a fresh session.")
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        print("Rotation stopped: secure interactive input was unavailable or cancelled.", file=sys.stderr)
        return 1
    except PyMongoError:
        print("MongoDB operation failed. Check the target before retrying; the write outcome may be unknown.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())