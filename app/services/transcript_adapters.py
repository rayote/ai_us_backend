from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "transcript-v1"


class UnsupportedTranscriptError(ValueError):
    pass


def normalize_export(tool: str | None, filename: str, content: bytes, participant_id: str) -> tuple[str, str, dict[str, Any], list[str]]:
    name = filename.lower()
    if "chatgpt" in (tool or "").lower() or name == "conversations.json":
        return "chatgpt-json", "chatgpt-json-v1", _parse_chatgpt(content, participant_id), []
    if "grok" in (tool or "").lower() or "grok" in name:
        return "grok-json", "grok-json-v1", _parse_grok(content, participant_id), []
    raise UnsupportedTranscriptError(f"{filename} 파일 형식에 대한 parser adapter가 아직 없습니다.")


def _iso(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=UTC).astimezone(KST).isoformat() if value else None
    except (TypeError, ValueError, OSError):
        return None


def _summary(participant_id: str, platform: str, source_type: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    starts = [item["sessionMetadata"]["startTime"] for item in sessions if item["sessionMetadata"].get("startTime")]
    ends = [item["sessionMetadata"]["endTime"] for item in sessions if item["sessionMetadata"].get("endTime")]
    return {
        "participantId": participant_id,
        "platform": platform,
        "sourceType": source_type,
        "schemaVersion": SCHEMA_VERSION,
        "parsedAt": datetime.now(UTC).isoformat(),
        "summary": {"totalSessions": len(sessions), "totalTurns": sum(len(item["turns"]) for item in sessions), "firstActivityTime": min(starts) if starts else None, "lastActivityTime": max(ends) if ends else None},
        "sessions": sessions,
    }


def _parse_chatgpt(content: bytes, participant_id: str) -> dict[str, Any]:
    conversations = json.loads(content.decode("utf-8-sig"))
    if not isinstance(conversations, list):
        raise ValueError("ChatGPT conversations.json은 배열이어야 합니다.")
    sessions: list[dict[str, Any]] = []
    for conversation in conversations:
        if not isinstance(conversation, dict):
            continue
        mapping = conversation.get("mapping") or {}
        current = conversation.get("current_node")
        path: list[dict[str, Any]] = []
        while current and isinstance(mapping, dict):
            node = mapping.get(current)
            if not isinstance(node, dict):
                break
            path.append(node)
            current = node.get("parent")
        path.reverse()
        turns: list[dict[str, Any]] = []
        for node in path:
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            role = message.get("author", {}).get("role")
            if role not in {"user", "assistant"}:
                continue
            parts = message.get("content", {}).get("parts", [])
            text = "\n".join(part if isinstance(part, str) else str(part.get("text", "")) for part in parts if isinstance(part, (str, dict))).strip()
            if not text:
                continue
            metadata = message.get("metadata") or {}
            turns.append({"turnId": len(turns) + 1, "role": role, "content": text, "contentFormat": "markdown" if role == "assistant" else "plainText", "timestamp": _iso(message.get("create_time")), "turnMetadata": {"modelSlug": metadata.get("model_slug")} if metadata.get("model_slug") else None})
        if turns:
            sessions.append(_session("chatgpt", conversation.get("conversation_id") or conversation.get("id") or "unknown", conversation.get("title") or "", turns))
    return _summary(participant_id, "chatgpt", "chatgpt_export_json", sessions)


def _parse_grok(content: bytes, participant_id: str) -> dict[str, Any]:
    data = json.loads(content.decode("utf-8-sig"))
    sessions: list[dict[str, Any]] = []
    for item in data.get("conversations", []) if isinstance(data, dict) else []:
        conversation = item.get("conversation") or {}
        turns: list[dict[str, Any]] = []
        responses = [row.get("response") or {} for row in item.get("responses", []) if isinstance(row, dict)]
        for response in sorted(responses, key=lambda row: ((row.get("create_time") or {}).get("$date", {}).get("$numberLong", 0))):
            value = ((response.get("create_time") or {}).get("$date", {}) or {}).get("$numberLong")
            timestamp = _iso(float(value) / 1000) if value else None
            role = "user" if response.get("sender") == "human" else "assistant"
            metadata = response.get("metadata") or {}
            turns.append({"turnId": len(turns) + 1, "role": role, "content": response.get("message") or "", "contentFormat": "markdown" if role == "assistant" else "plainText", "timestamp": timestamp, "turnMetadata": {"model": response.get("model"), "errors": metadata.get("stream_errors")} if role == "assistant" else None})
        if turns:
            sessions.append(_session("grok", conversation.get("id") or "unknown", conversation.get("title") or "", turns))
    return _summary(participant_id, "grok", "grok_export_json", sessions)


def _session(platform: str, original_id: str, title: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [turn["timestamp"] for turn in turns if turn.get("timestamp")]
    start, end = (timestamps[0], timestamps[-1]) if timestamps else (None, None)
    duration = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()) if start and end else None
    return {"sessionId": f"{platform}_{original_id}", "originalSessionId": original_id, "sessionTitle": title, "sessionMetadata": {"startTime": start, "endTime": end, "durationSeconds": duration, "totalTurns": len(turns), "userTurns": sum(turn["role"] == "user" for turn in turns), "assistantTurns": sum(turn["role"] == "assistant" for turn in turns)}, "turns": turns}
