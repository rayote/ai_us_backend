from app.core.settings import Settings


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

    import pytest
    from fastapi import HTTPException
    from app.api.applications import create_application, participation_admission_status
    from app.api.survey import _require_survey_access

    monkeypatch.setenv("PARTICIPATION_PAUSED", "true")
    settings = Settings.from_environment()
    assert settings.participation_paused is True
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
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

    monkeypatch.setenv("PARTICIPATION_PAUSED", "false")
    assert Settings.from_environment().participation_paused is False
