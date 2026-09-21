from __future__ import annotations

from app.schemas.analytics import AnalyticsEvent, AnalyticsEventAccepted
from app.services.analytics import DailyMetricsRepository
from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.post("/events", response_model=AnalyticsEventAccepted)
async def track_event(event: AnalyticsEvent, request: Request) -> AnalyticsEventAccepted:
    repository: DailyMetricsRepository | None = getattr(request.app.state, "daily_metrics_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="분석 서비스를 준비 중입니다.")
    await repository.track(event)
    return AnalyticsEventAccepted(status="tracked")
