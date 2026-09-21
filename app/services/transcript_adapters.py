from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "transcript-v1"


class UnsupportedTranscriptError(ValueError):
    pass


def normalize_export(
    tool: str | None, filename: str, content: bytes, participant_id: str
) -> tuple[str, str, dict[str, Any], list[str]]:
    name = filename.lower()
    if "chatgpt" in (tool or "").lower() or name == "conversations.json":
        payload, warnings = _chatgpt_payload(content)
        return "chatgpt-json", "chatgpt-json-v1", _parse_chatgpt(payload, participant_id), warnings
    if "grok" in (tool or "").lower() or "grok" in name:
        payload, warning = _grok_payload(content)
        return "grok-json", "grok-json-v1", _parse_grok(payload, participant_id), warning
    if "gemini" in (tool or "").lower() or "takeout" in name:
        payload, source_name = _gemini_payload(content)
        return (
            "gemini-takeout-html",
            "gemini-takeout-html-v1",
            _parse_gemini(payload, participant_id),
            [f"ZIP 내부의 {source_name} 파일을 파싱했습니다."] if source_name else [],
        )
    raise UnsupportedTranscriptError(f"{filename} 파일 형식에 대한 parser adapter가 아직 없습니다.")


def _decode_json(content: bytes) -> Any:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return json.loads(content.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            errors.append(f"{encoding}: {error}")
    raise ValueError("JSON 파일 인코딩 또는 형식을 읽을 수 없습니다. " + " / ".join(errors))


def _chatgpt_payload(content: bytes) -> tuple[bytes, list[str]]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content, []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        source_name = next(
            (name for name in archive.namelist() if PurePosixPath(name).name.lower() == "conversations.json"),
            None,
        )
        if source_name is None:
            raise ValueError("ZIP 파일 안에서 ChatGPT conversations.json 파일을 찾지 못했습니다.")
        return archive.read(source_name), [f"ZIP 내부의 {source_name} 파일을 파싱했습니다."]


def _grok_payload(content: bytes) -> tuple[bytes, list[str]]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content, []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        candidates = [name for name in archive.namelist() if name.lower().endswith(".json")]
        if not candidates:
            raise ValueError("ZIP 파일 안에서 Grok JSON 내보내기 파일을 찾지 못했습니다.")
        preferred = next(
            (name for name in candidates if "grok" in name.lower() or "conversation" in name.lower()), candidates[0]
        )
        return archive.read(preferred), [f"ZIP 내부의 {preferred} 파일을 파싱했습니다."]


def _decode_html(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Gemini HTML 파일 인코딩을 읽을 수 없습니다.")


def _gemini_payload(content: bytes) -> tuple[bytes, str | None]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content, None
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        candidates = [name for name in archive.namelist() if name.lower().endswith((".html", ".htm"))]
        source_name = next(
            (
                name
                for name in candidates
                if "gemini" in name.lower() and PurePosixPath(name).name.lower() in {"myactivity.html", "내활동.html"}
            ),
            next((name for name in candidates if "gemini" in name.lower()), None),
        )
        if source_name is None:
            raise ValueError("ZIP 파일 안에서 Gemini 내 활동 HTML 파일을 찾지 못했습니다.")
        return archive.read(source_name), source_name


class _GeminiTakeoutParser(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    _BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.activities: list[tuple[list[str], list[str]]] = []
        self._stack: list[tuple[str, set[str]]] = []
        self._direct: list[str] | None = None
        self._blocks: list[str] | None = None
        self._block_parts: list[str] | None = None
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set(dict(attrs).get("class", "").split())
        self._stack.append((tag, classes))
        if "outer-cell" in classes:
            self._direct, self._blocks = [], []
        elif self._direct is not None and "content-cell" in classes:
            self._in_body = "mdl-typography--caption" not in classes and "mdl-typography--text-right" not in classes
        elif self._in_body and self._blocks is not None and tag in self._BLOCK_TAGS:
            self._block_parts = []
        if tag in self._VOID_TAGS:
            self._stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if self._block_parts is not None and tag in self._BLOCK_TAGS:
            text = " ".join(self._block_parts).strip()
            if text and self._blocks is not None:
                self._blocks.append(text)
            self._block_parts = None
        if tag == "div" and self._stack and "content-cell" in self._stack[-1][1]:
            self._in_body = False
        if tag == "div" and self._stack and "outer-cell" in self._stack[-1][1]:
            if self._direct is not None and self._blocks is not None:
                self.activities.append((self._direct, self._blocks))
            self._direct = None
            self._blocks = None
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text or self._direct is None or not self._in_body:
            return
        if self._block_parts is not None:
            self._block_parts.append(text)
        elif self._stack and "content-cell" in self._stack[-1][1]:
            self._direct.append(text)


def _gemini_timestamp(value: str) -> str | None:
    match = re.search(
        r"(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(오전|오후)\s*(\d{1,2}):(\d{2}):(\d{2})\s*KST",
        value,
    )
    if match is None:
        return None
    year, month, day, period, hour, minute, second = match.groups()
    hour_value = int(hour) % 12 + (12 if period == "오후" else 0)
    return datetime(
        int(year), int(month), int(day), hour_value, int(minute), int(second), tzinfo=KST
    ).isoformat()


def _parse_gemini(content: bytes, participant_id: str) -> dict[str, Any]:
    parser = _GeminiTakeoutParser()
    parser.feed(_decode_html(content))
    sessions: list[dict[str, Any]] = []
    for index, (direct, blocks) in enumerate(parser.activities, start=1):
        timestamp = next((_gemini_timestamp(value) for value in direct if _gemini_timestamp(value)), None)
        prompt = next((value for value in direct if _gemini_timestamp(value) is None), "")
        prompt = re.sub(r"\s*항목을 검색함\s*$", "", prompt).strip()
        response = "\n".join(blocks).strip()
        turns = []
        if prompt:
            turns.append({"turnId": 1, "role": "user", "content": prompt, "timestamp": timestamp})
        if response:
            turns.append(
                {"turnId": len(turns) + 1, "role": "assistant", "content": response, "timestamp": timestamp}
            )
        if turns:
            sessions.append(_session("gemini", f"takeout-{index}", prompt[:80], turns))
    if not sessions:
        raise ValueError("Gemini 내 활동 HTML에서 대화 내역을 찾지 못했습니다.")
    return _summary(participant_id, "gemini", "gemini_takeout_html", sessions)


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
        "summary": {
            "totalSessions": len(sessions),
            "totalTurns": sum(len(item["turns"]) for item in sessions),
            "firstActivityTime": min(starts) if starts else None,
            "lastActivityTime": max(ends) if ends else None,
        },
        "sessions": sessions,
    }


def _parse_chatgpt(content: bytes, participant_id: str) -> dict[str, Any]:
    conversations = _decode_json(content)
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
            text = "\n".join(
                part if isinstance(part, str) else str(part.get("text", ""))
                for part in parts
                if isinstance(part, (str, dict))
            ).strip()
            if not text:
                continue
            metadata = message.get("metadata") or {}
            turns.append(
                {
                    "turnId": len(turns) + 1,
                    "role": role,
                    "content": text,
                    "contentFormat": "markdown" if role == "assistant" else "plainText",
                    "timestamp": _iso(message.get("create_time")),
                    "turnMetadata": {"modelSlug": metadata.get("model_slug")} if metadata.get("model_slug") else None,
                }
            )
        if turns:
            sessions.append(
                _session(
                    "chatgpt",
                    conversation.get("conversation_id") or conversation.get("id") or "unknown",
                    conversation.get("title") or "",
                    turns,
                )
            )
    return _summary(participant_id, "chatgpt", "chatgpt_export_json", sessions)


def _parse_grok(content: bytes, participant_id: str) -> dict[str, Any]:
    data = _decode_json(content)
    sessions: list[dict[str, Any]] = []
    for item in data.get("conversations", []) if isinstance(data, dict) else []:
        conversation = item.get("conversation") or {}
        turns: list[dict[str, Any]] = []
        responses = [row.get("response") or {} for row in item.get("responses", []) if isinstance(row, dict)]
        for response in sorted(
            responses, key=lambda row: ((row.get("create_time") or {}).get("$date", {}).get("$numberLong", 0))
        ):
            value = ((response.get("create_time") or {}).get("$date", {}) or {}).get("$numberLong")
            timestamp = _iso(float(value) / 1000) if value else None
            role = "user" if response.get("sender") == "human" else "assistant"
            metadata = response.get("metadata") or {}
            turns.append(
                {
                    "turnId": len(turns) + 1,
                    "role": role,
                    "content": response.get("message") or "",
                    "contentFormat": "markdown" if role == "assistant" else "plainText",
                    "timestamp": timestamp,
                    "turnMetadata": (
                        {"model": response.get("model"), "errors": metadata.get("stream_errors")}
                        if role == "assistant"
                        else None
                    ),
                }
            )
        if turns:
            sessions.append(
                _session("grok", conversation.get("id") or "unknown", conversation.get("title") or "", turns)
            )
    return _summary(participant_id, "grok", "grok_export_json", sessions)


def _session(platform: str, original_id: str, title: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [turn["timestamp"] for turn in turns if turn.get("timestamp")]
    start, end = (timestamps[0], timestamps[-1]) if timestamps else (None, None)
    duration = (
        int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()) if start and end else None
    )
    return {
        "sessionId": f"{platform}_{original_id}",
        "originalSessionId": original_id,
        "sessionTitle": title,
        "sessionMetadata": {
            "startTime": start,
            "endTime": end,
            "durationSeconds": duration,
            "totalTurns": len(turns),
            "userTurns": sum(turn["role"] == "user" for turn in turns),
            "assistantTurns": sum(turn["role"] == "assistant" for turn in turns),
        },
        "turns": turns,
    }
