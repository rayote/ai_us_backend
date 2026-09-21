from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.schemas.analytics import AnalyticsEvent

KST = ZoneInfo("Asia/Seoul")
EVENT_FIELDS = {
    "landing_visit": "visits",
    "signup_started": "signupStarted",
    "signup_submitted": "signupCompleted",
    "login_success": "loginSuccess",
    "survey_opened": "surveyStarted",
    "survey_submitted": "surveyCompleted",
    "survey_heartbeat": "surveyHeartbeat",
    "chat_submission_created": "chatSubmissions",
}
METRIC_FIELDS = tuple(EVENT_FIELDS.values())


class DailyMetricsRepository(Protocol):
    async def track(self, event: AnalyticsEvent) -> None: ...

    async def summary(self, days: int = 30) -> dict[str, object]: ...


class MongoDailyMetricsRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def track(self, event: AnalyticsEvent) -> None:
        now = datetime.now(UTC)
        field = EVENT_FIELDS[event.event]
        visitor_hash = hashlib.sha256(event.visitor_id.encode()).hexdigest()[:24]
        await self._collection.update_one(
            {
                "date": now.astimezone(KST).date().isoformat(),
                "campaign": event.campaign or "(direct)",
                "utm_source": event.utm_source or "",
                "utm_medium": event.utm_medium or "",
            },
            {
                "$inc": {field: 1},
                "$addToSet": {
                    "visitor_ids": visitor_hash,
                    f"{field}_visitor_ids": visitor_hash,
                },
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def summary(self, days: int = 30) -> dict[str, object]:
        today = datetime.now(KST).date()
        start = today - timedelta(days=days - 1)
        documents = [document async for document in self._collection.find({"date": {"$gte": start.isoformat()}})]

        daily = {start + timedelta(days=offset): _empty_metrics() for offset in range(days)}
        daily_visitors: dict[date, set[str]] = {day: set() for day in daily}
        campaigns: dict[tuple[str, str, str], dict[str, Any]] = {}
        for document in documents:
            document_date = date.fromisoformat(document["date"])
            if document_date not in daily:
                continue
            _merge_counts(daily[document_date], document)
            daily_visitors[document_date].update(document.get("visitor_ids", []))
            campaign = document.get("campaign", "(direct)")
            if campaign != "(direct)":
                key = (campaign, document.get("utm_source", ""), document.get("utm_medium", ""))
                aggregate = campaigns.setdefault(
                    key,
                    {
                        "campaign": campaign,
                        "source": key[1],
                        "medium": key[2],
                        **_empty_metrics(),
                        "visitorIds": set(),
                    },
                )
                _merge_counts(aggregate, document)
                aggregate["visitorIds"].update(document.get("visitor_ids", []))

        trend = []
        for day, metrics in daily.items():
            trend.append({"date": day.isoformat(), **metrics, "uniqueVisitors": len(daily_visitors[day])})

        def period_summary(period_days: int) -> dict[str, int]:
            selected = trend[-period_days:]
            result = _empty_metrics()
            for item in selected:
                for field in METRIC_FIELDS:
                    result[field] += int(item[field])
            visitor_ids: set[str] = set()
            for day in list(daily)[-period_days:]:
                visitor_ids.update(daily_visitors[day])
            return {**result, "uniqueVisitors": len(visitor_ids)}

        def visits_between(start_index: int, end_index: int) -> int:
            return sum(int(item["visits"]) for item in trend[start_index:end_index])

        campaign_rows = []
        for aggregate in campaigns.values():
            visitor_ids = aggregate.pop("visitorIds")
            unique_visitors = len(visitor_ids)
            campaign_rows.append(
                {
                    **aggregate,
                    "uniqueVisitors": unique_visitors,
                    "completionRate": (
                        round(int(aggregate["surveyCompleted"]) / unique_visitors * 100, 1) if unique_visitors else 0
                    ),
                }
            )
        campaign_rows.sort(key=lambda item: int(item["visits"]), reverse=True)
        today_summary = period_summary(1)
        week_summary = period_summary(min(7, days))
        month_summary = period_summary(days)
        signup_base = int(month_summary["uniqueVisitors"])
        survey_base = int(month_summary["surveyStarted"])
        return {
            "traffic": {
                "today": today_summary,
                "last7Days": week_summary,
                "last30Days": month_summary,
                "signupConversionRate": (
                    round(int(month_summary["signupCompleted"]) / signup_base * 100, 1) if signup_base else 0
                ),
                "surveyCompletionRate": (
                    round(int(month_summary["surveyCompleted"]) / survey_base * 100, 1) if survey_base else 0
                ),
                "visitChange1Day": int(today_summary["visits"]) - visits_between(-2, -1),
                "visitChange7Days": visits_between(-7, len(trend)) - visits_between(-14, -7),
            },
            "trafficTrend": trend,
            "hasCampaignData": bool(campaign_rows),
            "campaigns": campaign_rows,
        }


def _empty_metrics() -> dict[str, int]:
    return {field: 0 for field in METRIC_FIELDS}


def _merge_counts(target: dict[str, Any], source: dict[str, Any]) -> None:
    for field in METRIC_FIELDS:
        target[field] = int(target.get(field, 0)) + int(source.get(field, 0))
