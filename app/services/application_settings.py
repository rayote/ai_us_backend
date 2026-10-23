from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Protocol


class ApplicationSettingsRepository(Protocol):
    async def auto_approval_enabled(self) -> bool: ...

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool: ...

    async def survey_rounds(self, t1_open: bool) -> dict[str, dict[str, Any]]: ...

    async def set_survey_round_open(
        self, survey_round: int, enabled: bool, updated_by: str
    ) -> None: ...

    async def update_survey_round(
        self, survey_round: int, changes: dict[str, Any], updated_by: str
    ) -> None: ...


def survey_configurations(definitions: list[Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[tuple[int, str], list[Any]] = {}
    for definition in definitions:
        if definition.part not in (1, 2) or not definition.question_count:
            continue
        family = re.sub(
            r"(^|-)(elem|elementary|secondary|all|common)(?=-|$)",
            r"\1",
            definition.survey_version,
        )
        family = re.sub(r"(^|-)part[12](?=-|$)", r"\1parts", family).strip("-")
        family = re.sub(r"-+", "-", family)
        grouped.setdefault((definition.survey_round, family), []).append(definition)
    result: dict[str, list[dict[str, Any]]] = {
        str(round_number): [] for round_number in range(1, 5)
    }
    for (round_number, family), records in sorted(grouped.items()):
        if str(round_number) not in result:
            continue
        identities = {(record.audience, record.part) for record in records}
        common = {("all", 1), ("all", 2)}
        separate = {
            ("elementary", 1),
            ("elementary", 2),
            ("secondary", 1),
            ("secondary", 2),
        }
        required = common if common.issubset(identities) else separate
        if not required.issubset(identities) or any(
            sum((record.audience, record.part) == identity for record in records) != 1
            for identity in required
        ):
            continue
        versions = [
            {
                "surveyVersion": record.survey_version,
                "audience": record.audience,
                "part": record.part,
            }
            for record in records
            if (record.audience, record.part) in required
        ]
        release = family.split("-parts-", 1)[-1]
        result[str(round_number)].append(
            {
                "id": family,
                "label": f"{round_number}회차 · {release} · "
                + ("공통" if required == common else "초등·중고등")
                + " 파트 1·2",
                "versions": versions,
            }
        )
    return result


def resolve_survey_rounds(
    records: dict[str, dict[str, Any]], configurations: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, Any]]:
    result = {}
    for key, record in records.items():
        available = configurations.get(key, [])
        legacy_id = {"1": "t1-parts-v2-0916", "2": "t2-parts-v9-1022"}.get(key)
        selected_id = record.get("configuration_id", legacy_id)
        selected = next((item for item in available if item["id"] == selected_id), None)
        ended = record.get("period_ended", key == "1" and not record["is_open"]) is True
        allowed = record["is_open"] is True and not ended
        state = (
            "preparing"
            if selected is None
            else "closed" if ended else "open" if allowed else "preparing"
        )
        result[key] = {
            **record,
            "configuration_id": selected_id if selected else None,
            "period_ended": ended,
            "submission_allowed": allowed,
            "is_open": state == "open",
            "state": state,
            "configurations": available,
            "selected_configuration": selected,
        }
    return result


def default_survey_rounds(t1_open: bool) -> dict[str, dict[str, Any]]:
    return {
        str(survey_round): {"is_open": enabled}
        for survey_round, enabled in [(1, t1_open), (2, True), (3, False), (4, False)]
    }


async def load_survey_rounds(
    repository: ApplicationSettingsRepository | None,
    t1_open: bool,
    definitions: Any = None,
    researchers: Any = None,
) -> dict[str, dict[str, Any]]:
    if repository is None:
        return default_survey_rounds(t1_open)
    records = await repository.survey_rounds(t1_open)
    if definitions is not None:
        records = resolve_survey_rounds(
            records, survey_configurations(await definitions.list_definitions())
        )
    if researchers is not None:
        usernames: dict[str, str | None] = {}
        for survey_round, record in records.items():
            updated_by = record.get("updated_by")
            if updated_by:
                if updated_by not in usernames:
                    usernames[updated_by] = await researchers.find_username_by_id(
                        updated_by
                    )
                records[survey_round] = {
                    **record,
                    "updated_by": usernames[updated_by] or updated_by,
                }
    return records


class MongoApplicationSettingsRepository:
    _DOCUMENT_ID = "participant_application"

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def auto_approval_enabled(self) -> bool:
        document = await self._collection.find_one({"_id": self._DOCUMENT_ID})
        return bool(document and document.get("auto_approval", False))

    async def set_auto_approval(self, enabled: bool, updated_by: str) -> bool:
        await self._collection.update_one(
            {"_id": self._DOCUMENT_ID},
            {
                "$set": {
                    "auto_approval": enabled,
                    "updated_at": datetime.now(UTC),
                    "updated_by": updated_by,
                }
            },
            upsert=True,
        )
        return enabled

    async def survey_rounds(self, t1_open: bool) -> dict[str, dict[str, Any]]:
        document = await self._collection.find_one({"_id": self._DOCUMENT_ID})
        stored = (document or {}).get("survey_rounds", {})
        rounds = default_survey_rounds(t1_open)
        for key, default in rounds.items():
            record = stored.get(key, {})
            rounds[key] = {
                **record,
                "is_open": record.get("is_open", default["is_open"]) is True,
                "updated_at": record.get("updated_at"),
                "updated_by": record.get("updated_by"),
            }
        return rounds

    async def set_survey_round_open(
        self, survey_round: int, enabled: bool, updated_by: str
    ) -> None:
        await self.update_survey_round(survey_round, {"is_open": enabled}, updated_by)

    async def update_survey_round(
        self, survey_round: int, changes: dict[str, Any], updated_by: str
    ) -> None:
        if survey_round not in range(1, 5):
            raise ValueError("설문 회차가 올바르지 않습니다.")
        if not changes or set(changes) - {
            "is_open",
            "configuration_id",
            "period_ended",
        }:
            raise ValueError("설문 설정 항목이 올바르지 않습니다.")
        prefix = f"survey_rounds.{survey_round}"
        await self._collection.update_one(
            {"_id": self._DOCUMENT_ID},
            {
                "$set": {
                    **{f"{prefix}.{key}": value for key, value in changes.items()},
                    f"{prefix}.updated_at": datetime.now(UTC),
                    f"{prefix}.updated_by": updated_by,
                }
            },
            upsert=True,
        )
