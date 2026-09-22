from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


def _turns(document: dict[str, Any]) -> list[dict[str, Any]]:
    direct = document.get("turns")
    if isinstance(direct, list):
        return [turn for turn in direct if isinstance(turn, dict)]
    return [
        turn
        for session in document.get("sessions", [])
        if isinstance(session, dict)
        for turn in session.get("turns", [])
        if isinstance(turn, dict)
    ]


def _normalized_text(turn: dict[str, Any]) -> str:
    return re.sub(r"\s+", "", str(turn.get("content") or "")).casefold()


def _similarity(expected: dict[str, Any], actual: dict[str, Any]) -> float:
    return SequenceMatcher(None, _normalized_text(expected), _normalized_text(actual)).ratio()


def _source_indexes(turn: dict[str, Any]) -> Any:
    return turn.get("sourceImageIndexes", turn.get("turnMetadata", {}).get("sourceImageIndexes"))


def evaluate_screenshot_extraction(
    expected_document: dict[str, Any], actual_document: dict[str, Any]
) -> dict[str, Any]:
    expected = _turns(expected_document)
    actual = _turns(actual_document)
    expected_count, actual_count = len(expected), len(actual)
    gap_score = -0.35
    scores = [[0.0] * (actual_count + 1) for _ in range(expected_count + 1)]
    operations: list[list[str | None]] = [[None] * (actual_count + 1) for _ in range(expected_count + 1)]
    for expected_index in range(1, expected_count + 1):
        scores[expected_index][0] = scores[expected_index - 1][0] + gap_score
        operations[expected_index][0] = "missing"
    for actual_index in range(1, actual_count + 1):
        scores[0][actual_index] = scores[0][actual_index - 1] + gap_score
        operations[0][actual_index] = "extra"

    for expected_index in range(1, expected_count + 1):
        for actual_index in range(1, actual_count + 1):
            expected_turn = expected[expected_index - 1]
            actual_turn = actual[actual_index - 1]
            role_bonus = 0.1 if expected_turn.get("role") == actual_turn.get("role") else -0.1
            pair_score = 2 * _similarity(expected_turn, actual_turn) - 1 + role_bonus
            choices = [
                (scores[expected_index - 1][actual_index - 1] + pair_score, "pair"),
                (scores[expected_index - 1][actual_index] + gap_score, "missing"),
                (scores[expected_index][actual_index - 1] + gap_score, "extra"),
            ]
            scores[expected_index][actual_index], operations[expected_index][actual_index] = max(choices)

    alignment: list[dict[str, Any]] = []
    expected_index, actual_index = expected_count, actual_count
    while expected_index or actual_index:
        operation = operations[expected_index][actual_index]
        if operation == "pair":
            expected_turn = expected[expected_index - 1]
            actual_turn = actual[actual_index - 1]
            alignment.append(
                {
                    "operation": operation,
                    "expectedTurn": expected_index,
                    "actualTurn": actual_index,
                    "textSimilarity": round(_similarity(expected_turn, actual_turn), 4),
                    "roleMatch": expected_turn.get("role") == actual_turn.get("role"),
                    "timestampMatch": expected_turn.get("timestamp") == actual_turn.get("timestamp"),
                    "sourceImageIndexesMatch": (_source_indexes(expected_turn) == _source_indexes(actual_turn)),
                }
            )
            expected_index -= 1
            actual_index -= 1
        elif operation == "missing":
            alignment.append({"operation": operation, "expectedTurn": expected_index})
            expected_index -= 1
        else:
            alignment.append({"operation": "extra", "actualTurn": actual_index})
            actual_index -= 1
    alignment.reverse()

    pairs = [item for item in alignment if item["operation"] == "pair"]
    similarities = [item["textSimilarity"] for item in pairs]
    return {
        "expectedTurns": expected_count,
        "actualTurns": actual_count,
        "alignedPairs": len(pairs),
        "missingTurns": sum(item["operation"] == "missing" for item in alignment),
        "extraTurns": sum(item["operation"] == "extra" for item in alignment),
        "roleAccuracy": round(sum(item["roleMatch"] for item in pairs) / len(pairs), 4) if pairs else None,
        "meanTextSimilarity": round(sum(similarities) / len(similarities), 4) if similarities else None,
        "exactNormalizedTexts": sum(value == 1 for value in similarities),
        "timestampMatches": sum(item["timestampMatch"] for item in pairs),
        "sourceImageIndexesMatches": sum(item["sourceImageIndexesMatch"] for item in pairs),
        "alignment": alignment,
    }
