from app.core.settings import Settings


def test_mongo_participation_pause_preserves_other_application_settings():
    import asyncio
    from unittest.mock import AsyncMock
    from app.services.application_settings import MongoApplicationSettingsRepository

    collection = AsyncMock()
    repository = MongoApplicationSettingsRepository(collection)
    result = asyncio.run(repository.set_participation_paused(True, "admin-1"))
    assert result["participation_paused"] is True
    filters, update = collection.update_one.await_args.args
    assert filters == {"_id": "participant_application"}
    assert set(update["$set"]) == {
        "participation_paused", "participation_paused_updated_at", "participation_paused_updated_by",
    }
    assert update["$set"]["participation_paused_updated_by"] == "admin-1"
    assert collection.update_one.await_args.kwargs == {"upsert": True}


def test_mongo_participation_pause_defaults_only_when_unset_and_rejects_invalid_values():
    import asyncio
    from unittest.mock import AsyncMock
    import pytest
    from app.services.application_settings import MongoApplicationSettingsRepository

    collection = AsyncMock()
    repository = MongoApplicationSettingsRepository(collection)
    for document, expected in [(None, False), ({}, False), ({"participation_paused": True}, True)]:
        collection.find_one.return_value = document
        assert asyncio.run(repository.participation_settings())["participation_paused"] is expected
    collection.find_one.return_value = {"participation_paused": "false"}
    with pytest.raises(ValueError):
        asyncio.run(repository.participation_settings())
    collection.find_one.side_effect = TimeoutError("settings unavailable")
    with pytest.raises(TimeoutError):
        asyncio.run(repository.participation_settings())


def test_admin_pause_is_dynamic_audited_and_read_only_for_researchers():
    from fastapi.testclient import TestClient
    from app.core.security import create_access_token
    from app.main import create_app
    from app.services.application_settings import MongoApplicationSettingsRepository

    class Collection:
        document = {"auto_approval": True, "survey_rounds": {"2": {"is_open": True}}}

        async def find_one(self, query, **kwargs):
            return dict(self.document)

        async def update_one(self, query, update, **kwargs):
            self.document.update(update["$set"])

    collection = Collection()
    secret = "test-secret-at-least-thirty-two-bytes"
    app = create_app(Settings("test", None, "ai_us_test", (), secret),
        application_settings_repository=MongoApplicationSettingsRepository(collection))
    headers = lambda role: {"Authorization": "Bearer " + create_access_token(role + "-1", role, secret, 60)}
    path = "/api/v1/admin/participation-settings"
    with TestClient(app) as client:
        assert client.get("/api/v1/researcher/participation-settings").status_code == 401
        assert client.put(path, headers=headers("researcher"), json={"participationPaused": True}).status_code == 403
        assert client.put(path, headers=headers("admin"), json={"participationPaused": "true"}).status_code == 422
        assert client.get("/api/v1/applications/status").json()["participationPaused"] is False
        changed = client.put(path, headers=headers("admin"), json={"participationPaused": True})
        assert changed.status_code == 200
        assert changed.json()["updatedBy"] == "admin-1"
        assert changed.json()["updatedAt"]
        assert client.get("/api/v1/applications/status").json()["participationPaused"] is True
        viewed = client.get("/api/v1/researcher/participation-settings", headers=headers("researcher"))
        assert viewed.json() == changed.json()
        assert collection.document["auto_approval"] is True
        assert collection.document["survey_rounds"] == {"2": {"is_open": True}}
        assert client.put(path, headers=headers("admin"), json={"participationPaused": False}).status_code == 200
        assert client.get("/api/v1/applications/status").json()["participationPaused"] is False
        app.state.application_settings_repository = None
        assert client.get("/api/v1/applications/status").status_code == 503
        assert client.get("/api/v1/researcher/participation-settings", headers=headers("admin")).status_code == 503


def test_settings_builds_mongodb_uri_from_separate_credentials(monkeypatch) -> None:
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setenv("MONGODB_HOST", "svc.sel3.cloudtype.app")
    monkeypatch.setenv("MONGODB_PORT", "32075")
    monkeypatch.setenv("MONGODB_USERNAME", "admin")
    monkeypatch.setenv("MONGODB_PASSWORD", "password@with#symbols")

    settings = Settings.from_environment()

    assert settings.mongodb_uri == (
        "mongodb://admin:password%40with%23symbols@svc.sel3.cloudtype.app:32075/?authSource=admin"
    )


def test_settings_loads_gemini_screenshot_configuration(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_SCREENSHOT_MODEL", "gemini-test-model")

    settings = Settings.from_environment()

    assert settings.gemini_api_key == "test-gemini-key"
    assert settings.gemini_screenshot_model == "gemini-test-model"


def test_participation_pause_blocks_new_admission_before_repository_access(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest
    from fastapi import HTTPException, Request
    from app.api.applications import create_application, participation_admission_status
    from app.api.survey import _require_survey_access

    monkeypatch.setenv("PARTICIPATION_PAUSED", "true")
    settings = Settings.from_environment()
    assert not hasattr(settings, "participation_paused")
    repository = AsyncMock()
    repository.participation_settings.return_value = {"participation_paused": True}
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, application_settings_repository=repository))
    request = Request({"type": "http", "app": app})
    result = asyncio.run(participation_admission_status(request))
    assert result["participationPaused"] is True
    assert result["noticeTitle"] == "[안내] 설문 접수 일시 중단"
    for action in (
        create_application(None, request),
        _require_survey_access(request, "existing", 2, "part2"),
    ):
        with pytest.raises(HTTPException) as error:
            asyncio.run(action)
        assert error.value.status_code == 403
        assert error.value.detail == result["notice"]

    repository.participation_settings.return_value = {"participation_paused": False}
    result = asyncio.run(participation_admission_status(Request({"type": "http", "app": app})))
    assert result["participationPaused"] is False
    repository.participation_settings.side_effect = TimeoutError("settings unavailable")
    with pytest.raises(HTTPException) as error:
        asyncio.run(create_application(None, Request({"type": "http", "app": app})))
    assert error.value.status_code == 503
