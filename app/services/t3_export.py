from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from datetime import UTC, datetime

from app.schemas.survey import SurveyDefinition, SurveyResponseRecord
from app.services.surveys import _answer_value, _detail_value, _format_detail_datetime, _format_kst


T3_VERSIONS = tuple(f"t3-{audience}-part{part}-v4-1122" for audience in ("elem", "secondary") for part in (1, 2))
PAGE_COUNTS = {"elementary": (8, 4), "secondary": (8, 5)}
METRICS = ("시작 시각(KST)", "제출 시각(KST)", "소요 시간(초)", "전체경과시간(초)", "활동시간(초)", "방문 페이지 수")
FLAG_COLUMNS = (
    "불성실_1-(1)", "불성실_2&3_시간기록없음", "불성실_2&3_범위밖값", "불성실_4-(1)",
    "불성실_4-(2)", "불성실_5-(1)", "불성실_5-(1)_불일치항목", "불성실_7-(1)",
    "불성실_8-(1)", "불성실_9-(1)", "불성실_기준해당",
)
AI_EXPERIENCE_KEYS = tuple(f"aiexp.q{number}" for number in range(2, 47) if number != 12)
STRAIGHT_LINE_SCALES = frozenset(("context", "aiexp", "smartphone", "kbat", "uls8", "phq9", "gad7", "evs",
    "airel", "support_ai", "baas", "lsns6", "dwbq", "sp2023", "kpmhs", "ssosh", "ssrph", "conceal"))
EMPTY_TEXT_BY_CATEGORY = {
    "없음": ("없음", "없다", "없어요", "없습니다", "해당 없음", "특별히 없음", "딱히 없음"),
    "모름": ("모름", "몰라", "몰라요", "모르겠다", "모르겠음", "모르겠어요", "모르겠습니다",
             "잘 모르겠다", "잘 모르겠음", "잘 모르겠어요", "잘 모르겠습니다", "잘 모름"),
}


def _cell(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _start(detail):
    value = _detail_value(detail, "startedAt", "started_at")
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _metrics(response, progress, pages):
    detail = (response.detail if response else progress.get("detail")) or {}
    start = _start(detail)
    end = response.submitted_at if response else None
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    duration = (end - start).total_seconds() if start and end else None
    history = {}
    for record in progress.get("page_history", []):
        page = record.get("page")
        if type(page) is int and 1 <= page <= pages:
            history[page] = _format_detail_datetime(record, "saved_at")
    return [
        _format_kst(start) if start else "", _format_kst(end) if end else "",
        round(duration) if duration is not None and duration >= 0 else "",
        _detail_value(detail, "wallClockSeconds", "wall_clock_seconds"),
        _detail_value(detail, "activeSeconds", "active_seconds"),
        _detail_value(detail, "visitedPageCount", "visited_page_count"),
        *(history.get(page, "") for page in range(1, pages + 1)),
    ]


def _same_contact(*answers):
    for values in answers:
        phones = [re.sub(r"\D", "", str(_answer_value(values, key) or ""))
                  for key in ("demo.contact_phone", "demo.guardianContact_phone")]
        if all(phones) and phones[0] == phones[1]:
            return True
    return False


def _number(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value)
    return None


def _has_answer(value):
    if isinstance(value, dict):
        return any(_has_answer(item) for item in value.values())
    if isinstance(value, list):
        return bool(value)
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _condition(condition, answers):
    value = answers.get(condition["key"])
    if field := condition.get("field"):
        value = _number(value.get(field)) if isinstance(value, dict) else None
    if "eq" in condition:
        return value == condition["eq"]
    if "in" in condition:
        return value in condition["in"]
    if "neq" in condition:
        return value is not None and value != condition["neq"]
    if "includesAny" in condition:
        return isinstance(value, list) and any(item in value for item in condition["includesAny"])
    return value is not None


def _visible(logic, answers):
    if not logic:
        return True
    mode = logic["type"]
    value = answers.get(logic.get("dependsOn"))
    if mode == "showIfEq":
        return value == logic["equals"]
    if mode == "showIf":
        return value is not None and value != logic["notEquals"]
    if mode == "showIfAll":
        return all(_condition(condition, answers) for condition in logic["conds"])
    if mode == "showIfGroups":
        return any(all(_condition(condition, answers) for condition in group) for group in logic["groups"])
    if mode == "showIfCount":
        return sum(answers.get(key) in logic["values"] for key in logic["deps"]) >= logic.get("min", 1)
    if mode == "showIfAny":
        return any(isinstance(answers.get(key), (int, float)) and answers[key] >= logic["gte"] for key in logic["deps"])
    if mode == "disqualifyIf":
        return True
    raise ValueError("Unknown survey question logic")


def _scales(definition):
    return (definition.spec or {}).get("scales", [])


def _visible_answers(definition, answers):
    filtered = {key: value for key, value in answers.items() if key in {item.key for item in definition.questions}}
    for scale in _scales(definition):
        for question in scale["questions"]:
            key = question["key"]
            active = _visible(scale.get("logic"), answers) and _visible(question.get("logic"), answers)
            if not active:
                for item in (question, *question.get("rows", []), *question.get("fields", [])):
                    filtered.pop(item["key"], None)
                    if extra := item.get("otherText"):
                        filtered.pop(extra["key"], None)
            for container in (question.get("detail"), question.get("other")):
                if container:
                    value = answers.get(key)
                    when = container["when"]
                    if not active or not (when in value if isinstance(value, list) else when == value):
                        filtered.pop(container["field"]["key"], None)
    return filtered


def _answered_count(definition, answers):
    return sum(
        _has_answer(answers.get(item["key"]))
        for scale in _scales(definition) for question in scale["questions"]
        for item in (question.get("rows", []) if question["type"] == "grid" else [question])
    )


def _normalized_text(value):
    return re.sub(r"[\s.]+", "", str(value or "")).casefold()


EMPTY_TEXT = frozenset(_normalized_text(value) for variants in EMPTY_TEXT_BY_CATEGORY.values() for value in variants)


def _demographic_differences(first, second):
    differences = []
    for key, label in (("gender", "성별"), ("age", "나이"), ("enrolled", "재학 여부"),
                       ("grade", "학교급·학년"), ("region", "지역"), ("name", "학교 이름")):
        left, right = first.get(f"demo.{key}"), second.get(f"demo.{key}")
        if not _has_answer(left) or not _has_answer(right):
            continue
        if key == "age":
            age1, age2 = _number(left), _number(right)
            differs = age1 is not None and age2 is not None and abs(age1 - age2) >= 3
        else:
            differs = _normalized_text(left) != _normalized_text(right)
        if differs:
            differences.append(label)
    return differences


def _straight_line(questions, answers, *, allow_extremes=False):
    ratings = [question for question in questions if question["type"] == "likert"]
    if not ratings or (value := _number(answers.get(ratings[0]["key"]))) is None:
        return False
    for question in ratings:
        bounds = question["scale"]
        if _number(answers.get(question["key"])) != value or not bounds["min"] <= value <= bounds["max"]:
            return False
        if not allow_extremes and value in (bounds["min"], bounds["max"]):
            return False
    return True


def _flags(definition1, definition2, first, second, metrics1, metrics2, part2_pages):
    contact = _same_contact(first, second)
    no_time = all(not metrics[0] and metrics[3] == metrics[4] == "" for metrics in (metrics1, metrics2))
    usage_values = [first.get(f"usetype.A{number}_1") for number in range(1, 12)]
    out_of_range = any(_has_answer(value) and _number(value) not in range(4) for value in usage_values)
    very_short = False
    rates = []
    for definition, answers, metrics, pages, limit in (
        (definition1, first, metrics1, 8, 60), (definition2, second, metrics2, part2_pages, 30),
    ):
        active = _number(metrics[4])
        if _number(metrics[5]) != pages or active is None or active < 0:
            continue
        very_short |= active <= limit
        if answered_count := _answered_count(definition, answers):
            rates.append(active / answered_count)
    seconds_per_item = int(any(rate < 2 for rate in rates)) if rates else ""
    demographic_differences = _demographic_differences(first, second)
    demographics = len(demographic_differences) >= 2
    constant_experience = all(_number(first.get(key)) == 3 for key in AI_EXPERIENCE_KEYS)
    open_text = Counter(_normalized_text(value) for value in (
        second.get("coping.ss1"), second.get("coping.ss2"), first.get("aiexp.b2"), first.get("context.q6"),
    ))
    repeated_text = any(count >= 3 and text and text not in EMPTY_TEXT for text, count in open_text.items())
    fne = [scale for scale in _scales(definition1) if scale["scaleId"] == "fne"]
    same_fne = len(fne) == 1 and len(fne[0]["questions"]) == 12 and _straight_line(fne[0]["questions"], first, allow_extremes=True)
    straight = same_fne and sum(
        _straight_line(scale["questions"], answers)
        for definition, answers in ((definition1, first), (definition2, second))
        for scale in _scales(definition) if scale["scaleId"] in STRAIGHT_LINE_SCALES
    ) >= 5
    overall = any((contact, no_time, out_of_range, very_short, demographics, constant_experience, repeated_text, straight))
    return (int(contact), int(no_time), int(out_of_range), int(very_short), seconds_per_item,
            "개별 검토 필요" if demographics else "", ", ".join(demographic_differences) if demographics else "",
            int(constant_experience), int(repeated_text), int(straight), int(overall))


def t3_merged_csv(
    definitions: list[SurveyDefinition], responses: list[SurveyResponseRecord],
    metadata: list[dict],
) -> str:
    definition_map = {definition.survey_version: definition for definition in definitions}
    if set(definition_map) != set(T3_VERSIONS) or len(definitions) != 4:
        raise ValueError("Missing T3 definitions")
    for version, definition in definition_map.items():
        expected_audience = "elementary" if "-elem-" in version else "secondary"
        expected_part = 1 if "-part1-" in version else 2
        if definition.survey_round != 3 or definition.audience != expected_audience or (definition.spec or {}).get("part") != expected_part:
            raise ValueError("Unexpected T3 definition metadata")
    return _merged_csv(definition_map, T3_VERSIONS, responses, metadata, 3)


def legacy_survey_part(version):
    match = re.fullmatch(r"t[12]-(?:elem-|elementary-|secondary-|all-|common-)?part([12])(?:-.+)?", version)
    return int(match[1]) if match else None


def legacy_merged_csv(survey_round, definitions, responses, metadata, school_levels):
    definition_map = {definition.survey_version: definition for definition in definitions}
    return _merged_csv(definition_map, sorted(definition_map), responses, metadata, survey_round, school_levels)


def _recorded_page_widths(definition_map, parts, metadata):
    widths = dict.fromkeys((1, 2), 0)
    for version, definition in definition_map.items():
        part = parts[version]
        if part in widths:
            for scale in _scales(definition):
                page = scale.get("page")
                if type(page) is int and 1 <= page <= 100:
                    widths[part] = max(widths[part], page)
    for item in metadata:
        part = parts.get(item["survey_version"])
        if part in widths:
            for event in item.get("page_history", []):
                page = event.get("page")
                if type(page) is int and 1 <= page <= 100:
                    widths[part] = max(widths[part], page)
    return widths[1], widths[2]


def _merged_csv(definition_map, versions, responses, metadata, survey_round, school_levels=None):
    t3 = survey_round == 3
    parts = {version: 1 if "-part1-" in version else 2 for version in versions} if t3 else {
        version: legacy_survey_part(version) for version in versions
    }
    columns = {
        part: list(dict.fromkeys(question.key for version in versions if parts[version] == part
            for question in sorted(definition_map[version].questions, key=lambda item: item.order)))
        for part in (1, 2)
    }
    overlap = set(columns[1]) & set(columns[2])
    snapshots = {(item["participant_id"], item["survey_version"]): item for item in metadata}
    pages1, pages2 = (8, 5) if t3 else _recorded_page_widths(definition_map, parts, metadata) if survey_round == 2 else (0, 0)
    finals = {}
    first = {}
    for response in responses:
        version = response.survey_version
        if response.survey_round != survey_round or version not in definition_map:
            raise ValueError("Unexpected response in survey export")
        key = response.participant_id, version
        if key in finals:
            raise ValueError("Duplicate final response for survey version")
        finals[key] = response
        if parts[version] == 1:
            previous = first.get(response.participant_id)
            if previous and t3:
                raise ValueError("Multiple T3 part 1 audiences for participant")
            if previous is None or (response.submitted_at.replace(tzinfo=response.submitted_at.tzinfo or UTC), version) > (
                previous.submitted_at.replace(tzinfo=previous.submitted_at.tzinfo or UTC), previous.survey_version
            ):
                first[response.participant_id] = response
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "참여자 ID", "설문지 구분", "파트 1 상태", "파트 2 상태", "보호자_당사자연락처동일",
        *(f"{column}_p{part}" for part, pages in ((1, pages1), (2, pages2))
          for column in (*METRICS, *(f"검사 {page} 저장 시각(KST)" for page in range(1, pages + 1)))),
        *(f"{key}_p{part}" if key in overlap else key for part in (1, 2) for key in columns[part]),
        *(FLAG_COLUMNS if t3 else ()),
    ])
    for participant_id, part1 in sorted(first.items()):
        version2 = re.sub(r"-part1(?=-|$)", "-part2", part1.survey_version, count=1)
        definition1 = definition_map[part1.survey_version]
        definition2 = definition_map.get(version2)
        if definition2 is not None and definition2.audience != definition1.audience:
            definition2 = None
        part2 = finals.get((participant_id, version2)) if definition2 is not None else None
        audience = definition1.audience
        progress1 = snapshots.get((participant_id, part1.survey_version), {})
        progress2 = snapshots.get((participant_id, version2), {}) if definition2 is not None else {}
        status2 = "시작 안 함"
        if part2:
            status2 = "최종 제출"
        elif progress2 and not progress2.get("completed"):
            status2 = "중간 저장"
        answers1 = _visible_answers(definition1, part1.answers)
        answers2 = _visible_answers(definition2, part2.answers) if part2 else {}
        part2_pages = PAGE_COUNTS[audience][1] if t3 else pages2
        metric1 = _metrics(part1, progress1, pages1)
        metric2 = _metrics(part2, progress2, part2_pages)
        flags = ()
        if t3:
            flags = _flags(definition1, definition2, answers1, answers2, metric1, metric2, part2_pages) if part2 else [""] * len(FLAG_COLUMNS)
        school = (school_levels or {}).get(participant_id)
        label = {"elementary": "초등용", "secondary": "중고등용"}.get(audience, "")
        if survey_round == 2:
            label = {"초등": "초등용", "중등": "중고등용", "고등": "중고등용"}.get(school, "")
        writer.writerow([
            _cell(participant_id), label,
            "최종 제출", status2, _same_contact(answers1, answers2),
            *metric1, *metric2, *([""] * (pages2 - part2_pages)),
            *(_cell(_answer_value(answers1, key)) for key in columns[1]),
            *(_cell(_answer_value(answers2, key)) for key in columns[2]),
            *flags,
        ])
    return output.getvalue()