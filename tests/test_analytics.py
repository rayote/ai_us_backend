import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta

from app.core.settings import Settings
from app.main import create_app
from app.schemas.analytics import AnalyticsEvent
from app.services.analytics import MongoDailyMetricsRepository
from app.services.surveys import MongoSurveySessionRepository
from fastapi.testclient import TestClient


class InMemoryCursor:
    def __init__(self, documents):
        self.documents = documents

    def __aiter__(self):
        self.index = 0
        return self

    async def __anext__(self):
        if self.index >= len(self.documents):
            raise StopAsyncIteration
        document = self.documents[self.index]
        self.index += 1
        return deepcopy(document)


class InMemoryCollection:
    def __init__(self):
        self.documents = []

    async def update_one(self, filters, update, upsert=False):
        document = next(
            (item for item in self.documents if all(item.get(key) == value for key, value in filters.items())), None
        )
        if document is None:
            document = dict(filters)
            document.update(update.get("$setOnInsert", {}))
            self.documents.append(document)
        for key, value in update.get("$inc", {}).items():
            document[key] = document.get(key, 0) + value
        for key, value in update.get("$addToSet", {}).items():
            values = document.setdefault(key, [])
            if value not in values:
                values.append(value)
        document.update(update.get("$set", {}))

    def find(self, filters):
        if "date" not in filters:
            return InMemoryCursor(self.documents)
        minimum_date = filters.get("date", {}).get("$gte", "")
        return InMemoryCursor([item for item in self.documents if item["date"] >= minimum_date])


def test_daily_metrics_separates_visits_from_unique_visitors() -> None:
    repository = MongoDailyMetricsRepository(InMemoryCollection())
    event = AnalyticsEvent(
        event="landing_visit",
        visitorId="browser-session-123",
        campaign="school-a",
        utmSource="bitly",
        utmMedium="short-link",
    )

    asyncio.run(repository.track(event))
    asyncio.run(repository.track(event))
    asyncio.run(
        repository.track(AnalyticsEvent(event="signup_started", visitorId="browser-session-123", campaign="school-a"))
    )
    asyncio.run(
        repository.track(
            AnalyticsEvent(event="signup_submitted", visitorId="browser-session-123", campaign="school-a")
        )
    )
    summary = asyncio.run(repository.summary())

    assert summary["traffic"]["today"]["visits"] == 2
    assert summary["traffic"]["today"]["uniqueVisitors"] == 1
    assert summary["traffic"]["today"]["signupStarted"] == 1
    assert summary["trafficTrend"][-1]["visits"] == 2
    assert summary["campaigns"][0]["campaign"] == "school-a"
    assert summary["campaigns"][0]["uniqueVisitors"] == 1
    assert summary["campaigns"][0]["source"] == "bitly"
    assert summary["campaigns"][0]["completionRate"] == 0
    assert summary["traffic"]["signupConversionRate"] == 100.0


def test_activity_summary_has_thirty_day_rolling_trend() -> None:
    now = datetime.now(UTC)
    collection = InMemoryCollection()
    collection.documents = [
        {"participant_id": "p1", "updated_at": now, "started_at": now, "active_seconds": 60},
        {
            "participant_id": "p2",
            "updated_at": now - timedelta(days=8),
            "started_at": now - timedelta(days=8),
            "active_seconds": 30,
        },
    ]

    summary = asyncio.run(MongoSurveySessionRepository(collection).activity_summary())

    assert len(summary["activityTrend"]) == 30
    assert summary["activityTrend"][-1]["dau"] == 1
    assert summary["activityTrend"][-1]["wau"] == 1
    assert summary["activityTrend"][-1]["mau"] == 2


class RecordingMetrics:
    def __init__(self) -> None:
        self.events = []

    async def track(self, event) -> None:
        self.events.append(event)


def test_public_analytics_endpoint_validates_and_tracks_events() -> None:
    metrics = RecordingMetrics()
    app = create_app(
        Settings("test", None, "ai_us_test", (), "test-secret-at-least-thirty-two-bytes", 60),
        daily_metrics_repository=metrics,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/analytics/events",
            json={
                "event": "landing_visit",
                "visitorId": "browser-session-123",
                "campaign": "school-a",
            },
        )
        invalid = client.post(
            "/api/v1/analytics/events",
            json={"event": "unknown", "visitorId": "browser-session-123"},
        )

    assert response.status_code == 200
    assert metrics.events[0].campaign == "school-a"
    assert invalid.status_code == 422
