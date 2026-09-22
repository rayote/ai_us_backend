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
