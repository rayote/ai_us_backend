from __future__ import annotations

from dataclasses import dataclass
from os import getenv


def _origins_from_environment(value: str) -> tuple[str, ...]:
    return tuple(origin.strip() for origin in value.split(",") if origin.strip())


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
            mongodb_uri=getenv("MONGODB_URI"),
            database_name=getenv("DATABASE_NAME", "ai_us"),
            frontend_origins=_origins_from_environment(getenv("FRONTEND_ORIGINS", "")),
            jwt_secret=getenv("JWT_SECRET"),
            jwt_expiration_minutes=int(getenv("JWT_EXPIRATION_MINUTES", "60")),
            researcher_bootstrap_username=getenv("RESEARCHER_BOOTSTRAP_USERNAME"),
            researcher_bootstrap_password=getenv("RESEARCHER_BOOTSTRAP_PASSWORD"),
        )
