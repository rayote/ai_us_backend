from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def add_reward_question(survey_sets: dict) -> dict:
    updated = deepcopy(survey_sets)
    parts = [
        part
        for entries in updated.values()
        for part in entries
        if part.get("part") == 2
    ]
    if len(parts) != 1:
        raise ValueError("T2 공통 파트 2 정의 한 건이 필요합니다.")
    part = parts[0]
    if part.get("surveyVersion") != "t2-part2-v9-1022":
        raise ValueError("예상한 T2 버전이 아닙니다.")
    followups = [
        scale for scale in part["scales"] if scale.get("scaleId") == "followup"
    ]
    if len(followups) != 1:
        raise ValueError("기존 다음 조사 참여 문항을 먼저 확인해 주세요.")
    followup = followups[0]
    if not any(
        question.get("key") == "followup.q1" for question in followup["questions"]
    ):
        raise ValueError("기존 followup.q1 문항이 없습니다.")
    reward = {
        "scaleId": "reward",
        "scaleName": "참여 사례 선택",
        "page": followup["page"],
        "order": followup["order"] - 0.5,
        "questions": [
            {
                "key": "reward.gifticon",
                "no": "1",
                "text": "설문 참여 사례(1만 원 상당 모바일 상품권)로 받고 싶은 것을 하나 골라 주세요.",
                "required": True,
                "type": "single",
                "csvAppend": True,
                "csvColumn": "참여 사례 선택 | 선호 기프티콘 (1=GS25, 2=신세계, 3=다이소, 4=네이버페이, 5=올리브영)",
                "helper": [
                    "※ 상품권은 설문 완료 후 평일에 적어 주신 휴대폰 번호로 발송됩니다. 주말이나 공휴일에 설문을 마치면 다음 평일에 받게 됩니다."
                ],
                "options": [
                    {
                        "value": 1,
                        "label": "GS25 편의점 모바일 상품권 1만 원 — 발송 가능 시간: 상시",
                    },
                    {
                        "value": 2,
                        "label": "신세계상품권(이마트·신세계백화점 겸용) 1만 원 — 발송 가능 시간: 평일 9시~18시 (공휴일·주말 제외)",
                    },
                    {
                        "value": 3,
                        "label": "다이소 모바일 금액권 1만 원 — 발송 가능 시간: 평일 9시 ~ 19시",
                    },
                    {
                        "value": 4,
                        "label": "네이버페이 포인트 1만 원 — 발송 가능 시간: 평일 9시~18시 (공휴일·주말 제외)",
                    },
                    {
                        "value": 5,
                        "label": "올리브영 기프트카드 1만 원 — 발송 가능 시간: 평일 9시~18시 (공휴일·주말 제외)",
                    },
                ],
            }
        ],
    }
    existing = [scale for scale in part["scales"] if scale.get("scaleId") == "reward"]
    if existing:
        if existing != [reward]:
            raise ValueError("기존 참여 사례 선택 정의가 다릅니다. 덮어쓰지 않습니다.")
        return updated
    if any(
        question.get("key") == "reward.gifticon"
        for scale in part["scales"]
        for question in scale.get("questions", [])
    ):
        raise ValueError("기프티콘 문항 키가 이미 사용 중입니다.")
    part["scales"].insert(part["scales"].index(followup), reward)
    return updated


def main() -> int:
    from app.schemas.survey import SurveyDefinitionCreate
    from register_frontend_surveys import DEFAULT_FRONTEND_INDEX, extract_survey_sets

    parser = argparse.ArgumentParser(
        description="Add the T2 gift preference without changing existing question keys or versions."
    )
    parser.add_argument("--frontend-index", type=Path, default=DEFAULT_FRONTEND_INDEX)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    original = extract_survey_sets(args.frontend_index, 2)
    updated = add_reward_question(original)
    assert add_reward_question(updated) == updated
    for original_parts, updated_parts in zip(
        original.values(), updated.values(), strict=True
    ):
        for before, after in zip(original_parts, updated_parts, strict=True):
            old = SurveyDefinitionCreate.model_validate(before)
            new = SurveyDefinitionCreate.model_validate(after)
            assert old.survey_version == new.survey_version
            if before == after:
                assert old.questions == new.questions
            else:
                assert new.questions[:-1] == old.questions
                assert new.questions[-1].key == "reward.gifticon"
            print(
                f"{new.survey_version}: {len(old.questions)} -> {len(new.questions)} answer fields"
            )
    if args.dry_run or original == updated:
        print(
            "Dry run passed; no files changed"
            if args.dry_run
            else "Already applied; no files changed"
        )
        return 0
    html = args.frontend_index.read_text(encoding="utf-8")
    match = re.search(
        r"var\s+SURVEY_SETS_T2\s*=([\s\S]*?);\n\s*var\s+SURVEY_ROUND\s*=", html
    )
    if match is None:
        raise ValueError("T2 정의를 찾을 수 없습니다.")
    serialized = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
    assert "</script" not in serialized.lower()
    args.frontend_index.write_text(
        html[: match.start(1)] + serialized + html[match.end(1) :], encoding="utf-8"
    )
    print("Updated frontend T2 data only; no database changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
