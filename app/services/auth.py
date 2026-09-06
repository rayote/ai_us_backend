from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.security import verify_password
from bson import ObjectId
from pymongo.errors import DuplicateKeyError


@dataclass(frozen=True)
class ParticipantAccount:
    participant_id: str
    phone: str
    password_hash: str
    must_change_password: bool


class ParticipantAccountRepository(Protocol):
    async def find_by_phone(self, phone: str) -> ParticipantAccount | None: ...

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None: ...

    async def update_password(self, participant_id: str, password_hash: str) -> bool: ...

    async def create(self, phone: str, password_hash: str) -> bool: ...

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int
    ) -> bool: ...


class MongoParticipantAccountRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def find_by_phone(self, phone: str) -> ParticipantAccount | None:
        document = await self._collection.find_one({"phone_normalized": phone, "role": "participant"})
        return self._account_from_document(document)

    async def find_by_id(self, participant_id: str) -> ParticipantAccount | None:
        document = await self._collection.find_one({"_id": ObjectId(participant_id), "role": "participant"})
        return self._account_from_document(document)

    @staticmethod
    def _account_from_document(document: dict[str, Any] | None) -> ParticipantAccount | None:
        if document is None:
            return None
        return ParticipantAccount(
            participant_id=str(document["_id"]),
            phone=document["phone_normalized"],
            password_hash=document["password_hash"],
            must_change_password=document["must_change_password"],
        )

    async def update_password(self, participant_id: str, password_hash: str) -> bool:
        result = await self._collection.update_one(
            {"_id": ObjectId(participant_id), "role": "participant"},
            {"$set": {"password_hash": password_hash, "must_change_password": False}},
        )
        return result.modified_count == 1

    async def create(self, phone: str, password_hash: str) -> bool:
        try:
            await self._collection.insert_one(
                {
                    "phone_normalized": phone,
                    "role": "participant",
                    "password_hash": password_hash,
                    "must_change_password": True,
                }
            )
        except DuplicateKeyError:
            return False
        return True

    async def create_imported(
        self, phone: str, password_hash: str, name: str, school_level: str, grade: int
    ) -> bool:
        try:
            await self._collection.insert_one(
                {
                    "phone_normalized": phone,
                    "role": "participant",
                    "password_hash": password_hash,
                    "must_change_password": True,
                    "name": name,
                    "school_level": school_level,
                    "grade": grade,
                }
            )
        except DuplicateKeyError:
            return False
        return True


@dataclass(frozen=True)
class ResearcherAccount:
    researcher_id: str
    username: str
    password_hash: str
    role: str


class ResearcherAccountRepository(Protocol):
    async def find_by_username(self, username: str) -> ResearcherAccount | None: ...

    async def ensure_bootstrap(self, username: str, password_hash: str) -> None: ...

    async def create(self, username: str, password_hash: str, role: str) -> str | None: ...


class MongoResearcherAccountRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def find_by_username(self, username: str) -> ResearcherAccount | None:
        document = await self._collection.find_one({"username": username})
        if document is None:
            return None
        return ResearcherAccount(
            str(document["_id"]), document["username"], document["password_hash"], document["role"]
        )

    async def ensure_bootstrap(self, username: str, password_hash: str) -> None:
        await self._collection.update_one(
            {"username": username},
            {"$setOnInsert": {"username": username, "password_hash": password_hash, "role": "admin"}},
            upsert=True,
        )

    async def create(self, username: str, password_hash: str, role: str) -> str | None:
        try:
            result = await self._collection.insert_one(
                {"username": username, "password_hash": password_hash, "role": role}
            )
        except DuplicateKeyError:
            return None
        return str(result.inserted_id)


class InvalidCredentialsError(Exception):
    """Raised when a participant cannot be authenticated."""


class ParticipantAuthenticationService:
    def __init__(self, repository: ParticipantAccountRepository) -> None:
        self._repository = repository

    async def authenticate(self, phone: str, password: str) -> ParticipantAccount:
        account = await self._repository.find_by_phone(phone)
        if account is None or not verify_password(password, account.password_hash):
            raise InvalidCredentialsError
        return account

    async def change_password(self, participant_id: str, current_password: str, new_password: str) -> None:
        account = await self._repository.find_by_id(participant_id)
        if account is None or not verify_password(current_password, account.password_hash):
            raise InvalidCredentialsError
        from app.core.security import hash_password

        if not await self._repository.update_password(participant_id, hash_password(new_password)):
            raise InvalidCredentialsError


class ResearcherAuthenticationService:
    def __init__(self, repository: ResearcherAccountRepository) -> None:
        self._repository = repository

    async def authenticate(self, username: str, password: str) -> ResearcherAccount:
        account = await self._repository.find_by_username(username)
        if account is None or not verify_password(password, account.password_hash):
            raise InvalidCredentialsError
        return account


class ResearcherAdministrationService:
    def __init__(self, repository: ResearcherAccountRepository) -> None:
        self._repository = repository

    async def create_researcher(self, username: str, password: str) -> str | None:
        from app.core.security import hash_password

        return await self._repository.create(username, hash_password(password), "researcher")
