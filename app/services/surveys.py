from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.schemas.survey import SurveyDefinition, SurveyDefinitionCreate, SurveyDefinitionSummary, SurveyResponseRecord
from pymongo.errors import DuplicateKeyError


class DuplicateSurveyDefinitionError(Exception):
    """Raised when a survey round and version already have a definition."""


class SurveyDefinitionRepository(Protocol):
    async def create(self, definition: SurveyDefinitionCreate) -> SurveyDefinition: ...

    async def replace(self, definition: SurveyDefinitionCreate) -> SurveyDefinition: ...

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None: ...

    async def list_definitions(self) -> list[SurveyDefinitionSummary]: ...


class SurveyResponseRepository(Protocol):
    async def create_response(self, response: SurveyResponseRecord) -> None: ...

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]: ...

    async def list_versions_for_participant(self, participant_id: str, survey_round: int) -> list[str]: ...

    async def list_all_responses(self) -> list[SurveyResponseRecord]: ...


class SurveySessionRepository(Protocol):
    async def heartbeat(self, participant_id: str, heartbeat: Any) -> None: ...

    async def activity_summary(self) -> dict[str, object]: ...


def _definition_from_document(document: dict[str, Any]) -> SurveyDefinition:
    return SurveyDefinition(
        surveyRound=document["survey_round"],
        surveyVersion=document["survey_version"],
        audience=document.get("audience"),
        questions=document["questions"],
        spec=document.get("spec"),
        createdAt=document["created_at"],
    )


def _definition_summary_from_document(document: dict[str, Any]) -> SurveyDefinitionSummary:
    spec = document.get("spec") or {}
    meta = spec.get("_meta") if isinstance(spec, dict) else None
    return SurveyDefinitionSummary(
        surveyRound=document["survey_round"],
        surveyVersion=document["survey_version"],
        audience=document.get("audience"),
        part=spec.get("part") if isinstance(spec, dict) else None,
        title=meta.get("title") if isinstance(meta, dict) else None,
        questionCount=len(document.get("questions") or []),
        createdAt=document["created_at"],
    )


def _response_from_document(document: dict[str, Any]) -> SurveyResponseRecord:
    return SurveyResponseRecord(
        participantId=str(document["participant_id"]),
        surveyRound=document["survey_round"],
        surveyVersion=document["survey_version"],
        answers=document["answers"],
        submittedAt=document["submitted_at"],
        detail=document.get("detail"),
    )


class MongoSurveyDefinitionRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create(self, definition: SurveyDefinitionCreate) -> SurveyDefinition:
        document = {
            "survey_round": definition.survey_round,
            "survey_version": definition.survey_version,
            "questions": [question.model_dump(by_alias=True) for question in definition.questions],
            "created_at": datetime.now(UTC),
        }
        if definition.audience is not None:
            document["audience"] = definition.audience
        if definition.raw_spec is not None:
            document["spec"] = definition.raw_spec
        try:
            await self._collection.insert_one(document)
        except DuplicateKeyError as error:
            raise DuplicateSurveyDefinitionError from error
        return _definition_from_document(document)

    async def replace(self, definition: SurveyDefinitionCreate) -> SurveyDefinition:
        document = {
            "survey_round": definition.survey_round,
            "survey_version": definition.survey_version,
            "questions": [question.model_dump(by_alias=True) for question in definition.questions],
            "created_at": datetime.now(UTC),
        }
        if definition.audience is not None:
            document["audience"] = definition.audience
        if definition.raw_spec is not None:
            document["spec"] = definition.raw_spec
        await self._collection.replace_one(
            {"survey_round": definition.survey_round, "survey_version": definition.survey_version},
            document,
            upsert=True,
        )
        return _definition_from_document(document)

    async def get(self, survey_round: int, survey_version: str) -> SurveyDefinition | None:
        document = await self._collection.find_one({"survey_round": survey_round, "survey_version": survey_version})
        return _definition_from_document(document) if document else None

    async def list_definitions(self) -> list[SurveyDefinitionSummary]:
        cursor = self._collection.find({}).sort("survey_round", 1)
        definitions = [_definition_summary_from_document(document) async for document in cursor]
        return sorted(definitions, key=lambda definition: (definition.survey_round, definition.survey_version))


class MongoSurveyResponseRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def create_response(self, response: SurveyResponseRecord) -> None:
        await self._collection.insert_one(
            {
                "participant_id": response.participant_id,
                "survey_round": response.survey_round,
                "survey_version": response.survey_version,
                "answers": response.answers,
                "submitted_at": response.submitted_at,
                "detail": response.detail,
            }
        )

    async def list_responses(self, survey_round: int, survey_version: str) -> list[SurveyResponseRecord]:
        cursor = self._collection.find({"survey_round": survey_round, "survey_version": survey_version}).sort(
            "submitted_at", 1
        )
        return [_response_from_document(document) async for document in cursor]

    async def list_versions_for_participant(self, participant_id: str, survey_round: int) -> list[str]:
        cursor = self._collection.find({"participant_id": participant_id, "survey_round": survey_round})
        return [document["survey_version"] async for document in cursor]

    async def list_all_responses(self) -> list[SurveyResponseRecord]:
        cursor = self._collection.find({}).sort("submitted_at", 1)
        return [_response_from_document(document) async for document in cursor]


class MongoSurveySessionRepository:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def heartbeat(self, participant_id: str, heartbeat: Any) -> None:
        await self._collection.update_one(
            {"session_id": heartbeat.session_id},
            {
                "$set": {
                    "participant_id": participant_id,
                    "survey_round": heartbeat.survey_round,
                    "survey_version": heartbeat.survey_version,
                    "started_at": heartbeat.started_at,
                    "last_heartbeat_at": heartbeat.last_heartbeat_at,
                    "active_seconds": heartbeat.active_seconds,
                    "resume_count": heartbeat.resume_count,
                    "current_page": heartbeat.current_page,
                    "visited_page_count": heartbeat.visited_page_count,
                    "total_page_count": heartbeat.total_page_count,
                    "navigation_count": heartbeat.navigation_count,
                    "updated_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def activity_summary(self) -> dict[str, object]:
        now = datetime.now(UTC)
        sessions = [document async for document in self._collection.find({})]

        def active_participants_since(seconds: int) -> set[str]:
            cutoff = now.timestamp() - seconds
            return {
                str(document["participant_id"])
                for document in sessions
                if document.get("updated_at") and document["updated_at"].timestamp() >= cutoff
            }

        active_now = active_participants_since(60)
        daily = active_participants_since(24 * 60 * 60)
        weekly = active_participants_since(7 * 24 * 60 * 60)
        monthly = active_participants_since(30 * 24 * 60 * 60)
        active_seconds = [int(document.get("active_seconds", 0)) for document in sessions]
        participant_ids = {str(document["participant_id"]) for document in sessions if document.get("participant_id")}
        started_at_values = [document.get("started_at") for document in sessions if document.get("started_at")]
        first_started_at = min(started_at_values) if started_at_values else None
        return {
            "dau": len(daily),
            "wau": len(weekly),
            "mau": len(monthly),
            "activeNow": len(active_now),
            "sessionCount": len(sessions),
            "totalSessions": len(sessions),
            "totalParticipants": len(participant_ids),
            "averageActiveSeconds": round(sum(active_seconds) / len(active_seconds)) if active_seconds else 0,
            "hasCampaignData": False,
            "campaigns": [],
            "firstStartedAt": first_started_at.isoformat() if first_started_at else None,
            "generatedAt": now.isoformat(),
        }


def survey_responses_to_csv(
    definition: SurveyDefinition,
    responses: list[SurveyResponseRecord],
    participant_profiles: dict[str, tuple[str, str | None, int | None]] | None = None,
) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    questions = sorted(definition.questions, key=lambda question: question.order)
    writer.writerow(
        [
            "아이디(휴대폰)",
            "학교급",
            "학년",
            "surveyRound",
            "surveyVersion",
            "응답 일시(KST)",
            "설문 시작 일시(KST)",
            "전체 경과시간(초)",
            "활동시간(초)",
            "재개 횟수",
            "마지막 방문 페이지",
            "방문한 페이지 수",
            "전체 페이지 수",
            "페이지 이동 횟수",
            *[question.csv_column for question in questions],
        ]
    )
    for response in responses:
        profile = (participant_profiles or {}).get(response.participant_id, ("-", None, None))
        writer.writerow(
            [
                _excel_text(profile[0]),
                profile[1] or "",
                profile[2] or "",
                response.survey_round,
                response.survey_version,
                _format_kst(response.submitted_at),
                _format_detail_datetime(response.detail, "startedAt", "started_at"),
                _detail_value(response.detail, "wallClockSeconds", "wall_clock_seconds"),
                _detail_value(response.detail, "activeSeconds", "active_seconds"),
                _detail_value(response.detail, "resumeCount", "resume_count"),
                _detail_value(response.detail, "pageCount", "page_count"),
                _detail_value(response.detail, "visitedPageCount", "visited_page_count"),
                _detail_value(response.detail, "totalPageCount", "total_page_count"),
                _detail_value(response.detail, "navigationCount", "navigation_count"),
                *[_csv_value(_answer_value(response.answers, question.key)) for question in questions],
            ]
        )
    return output.getvalue()


def _answer_value(answers: dict[str, object], key: str) -> object | None:
    if key in answers:
        return answers[key]
    for value in answers.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return None


def _excel_text(value: str) -> str:
    return f'="{value}"' if value and value != "-" else value


def _format_kst(value: datetime) -> str:
    return value.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")


def _detail_value(detail: dict[str, object] | None, *keys: str) -> object:
    if not detail:
        return ""
    for key in keys:
        if key in detail and detail[key] is not None:
            return detail[key]
    return ""


def _format_detail_datetime(detail: dict[str, object] | None, *keys: str) -> str:
    value = _detail_value(detail, *keys)
    if value == "":
        return ""
    if isinstance(value, datetime):
        return _format_kst(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return _format_kst(parsed)


def _csv_value(value: object | None) -> object:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    return value
