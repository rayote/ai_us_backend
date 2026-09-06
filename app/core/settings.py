from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from urllib.parse import quote


def _origins_from_environment(value: str) -> tuple[str, ...]:
    return tuple(origin.strip() for origin in value.split(",") if origin.strip())


def _mongodb_uri_from_environment() -> str | None:
    legacy_uri = getenv("MONGODB_URI")
    if legacy_uri:
        return legacy_uri

    host = getenv("MONGODB_HOST")
    username = getenv("MONGODB_USERNAME")
    password = getenv("MONGODB_PASSWORD")
    if not host or not username or password is None:
        return None

    port = getenv("MONGODB_PORT", "27017")
    return f"mongodb://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{port}/?authSource=admin"


@dataclass(frozen=True)
class Settings:
    environment: str
    mongodb_uri: str | None
    database_name: str
    frontend_origins: tuple[str, ...]
    jwt_secret: str | None = None
    jwt_expiration_minutes: int = 60
    researcher_bootstrap_username: str | None = None
    researcher_bootstrap_password: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            environment=getenv("APP_ENV", "development"),
            mongodb_uri=_mongodb_uri_from_environment(),
            database_name=getenv("DATABASE_NAME", "ai_us"),
            frontend_origins=_origins_from_environment(getenv("FRONTEND_ORIGINS", "")),
            jwt_secret=getenv("JWT_SECRET"),
            jwt_expiration_minutes=int(getenv("JWT_EXPIRATION_MINUTES", "60")),
            researcher_bootstrap_username=getenv("RESEARCHER_BOOTSTRAP_USERNAME"),
            researcher_bootstrap_password=getenv("RESEARCHER_BOOTSTRAP_PASSWORD"),
        )
