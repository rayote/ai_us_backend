from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "transcript-v1"


class UnsupportedTranscriptError(ValueError):
    pass


def normalize_export(
    tool: str | None, filename: str, content: bytes, participant_id: str
) -> tuple[str, str, dict[str, Any], list[str]]:
    name = filename.lower()
    selected_tool = (tool or "").lower()
    if "chatgpt" in selected_tool or (not selected_tool and name == "conversations.json"):
        payload, warnings = _chatgpt_payload(content)
        return (
            "chatgpt-json",
            "chatgpt-json-v1",
            _parse_chatgpt(payload, participant_id),
            warnings,
        )
    if "claude" in selected_tool or (not selected_tool and "claude" in name):
        payload, warnings = _claude_payload(content)
        normalized, parse_warnings = _parse_claude(payload, participant_id)
        return (
            "claude-json",
            "claude-json-v1",
            normalized,
            warnings + parse_warnings,
        )
    if "grok" in selected_tool or (not selected_tool and "grok" in name):
        payload, warning = _grok_payload(content)
        return (
            "grok-json",
            "grok-json-v1",
            _parse_grok(payload, participant_id),
            warning,
        )
    if "gemini" in selected_tool or (not selected_tool and "takeout" in name):
        payload, source_name = _gemini_payload(content)
        return (
            "gemini-takeout-html",
            "gemini-takeout-html-v2",
            _parse_gemini(payload, participant_id),
            [f"ZIP 내부의 {source_name} 파일을 파싱했습니다."] if source_name else [],
        )
    raise UnsupportedTranscriptError(
        f"{filename} 파일 형식에 대한 parser adapter가 아직 없습니다."
    )


def _decode_json(content: bytes) -> Any:
    errors: list[str] = []
    for encoding in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
        try:
            return json.loads(content.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            errors.append(f"{encoding}: {error}")
    raise ValueError(
        "JSON 파일 인코딩 또는 형식을 읽을 수 없습니다. " + " / ".join(errors)
    )


def _is_grok_export(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    conversations = payload.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        return False
    for item in conversations:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("conversation"), dict):
            return False
        responses = item.get("responses")
        if not isinstance(responses, list):
            return False
        if not all(
            isinstance(row, dict) and isinstance(row.get("response"), dict)
            for row in responses
        ):
            return False
    return True


def find_grok_export_member(archive: zipfile.ZipFile) -> str:
    candidates = [name for name in archive.namelist() if name.lower().endswith(".json")]
    candidates.sort(
        key=lambda name: (
            "grok" not in name.lower() and "conversation" not in name.lower(),
            name.lower(),
        )
    )
    for name in candidates:
        try:
            payload = _decode_json(archive.read(name))
        except ValueError:
            continue
        if _is_grok_export(payload):
            return name
    raise ValueError(
        "ZIP 파일 안에서 올바른 Grok 대화 내보내기 파일을 찾지 못했습니다."
    )


def _is_claude_export(payload: Any) -> bool:
    if not isinstance(payload, list) or not payload:
        return False
    for conversation in payload:
        if not isinstance(conversation, dict) or not isinstance(
            conversation.get("uuid"), str
        ):
            return False
        messages = conversation.get("chat_messages")
        if not isinstance(messages, list):
            return False
        for message in messages:
            if not isinstance(message, dict):
                return False
            if message.get("sender") not in {"human", "assistant"}:
                return False
    return True


def find_claude_export_member(archive: zipfile.ZipFile) -> str:
    candidates = [name for name in archive.namelist() if name.lower().endswith(".json")]
    candidates.sort(
        key=lambda name: (
            PurePosixPath(name).name.lower() != "conversations.json",
            name.lower(),
        )
    )
    for name in candidates:
        try:
            payload = _decode_json(archive.read(name))
        except ValueError:
            continue
        if _is_claude_export(payload):
            return name
    raise ValueError(
        "ZIP 파일 안에서 올바른 Claude conversations.json을 찾지 못했습니다."
    )


def _chatgpt_payload(content: bytes) -> tuple[bytes, list[str]]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content, []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        source_name = next(
            (
                name
                for name in archive.namelist()
                if PurePosixPath(name).name.lower() == "conversations.json"
            ),
            None,
        )
        if source_name is None:
            raise ValueError(
                "ZIP 파일 안에서 ChatGPT conversations.json 파일을 찾지 못했습니다."
            )
        return archive.read(source_name), [
            f"ZIP 내부의 {source_name} 파일을 파싱했습니다."
        ]


def _claude_payload(content: bytes) -> tuple[bytes, list[str]]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content, []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        source_name = find_claude_export_member(archive)
        return archive.read(source_name), [
            f"ZIP 내부의 {source_name} 파일을 파싱했습니다."
        ]


def _grok_payload(content: bytes) -> tuple[bytes, list[str]]:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return content, []
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        source_name = find_grok_export_member(archive)
        return archive.read(source_name), [
            f"ZIP 내부의 {source_name} 파일을 파싱했습니다."
        ]


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
        candidates = [
            name
            for name in archive.namelist()
            if name.lower().endswith((".html", ".htm"))
        ]
        source_name = next(
            (
                name
                for name in candidates
                if "gemini" in name.lower()
                and PurePosixPath(name).name.lower()
                in {"myactivity.html", "내활동.html"}
            ),
            next((name for name in candidates if "gemini" in name.lower()), None),
        )
        if source_name is None:
            raise ValueError(
                "ZIP 파일 안에서 Gemini 내 활동 HTML 파일을 찾지 못했습니다."
            )
        return archive.read(source_name), source_name


class _GeminiTakeoutParser(HTMLParser):
    _VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
    _BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.activities: list[tuple[list[str], list[str], list[str]]] = []
        self._stack: list[tuple[str, set[str]]] = []
        self._direct: list[str] | None = None
        self._blocks: list[str] | None = None
        self._block_parts: list[str] | None = None
        self._links: list[str] | None = None
        self._in_body = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set(dict(attrs).get("class", "").split())
        self._stack.append((tag, classes))
        if "outer-cell" in classes:
            self._direct, self._blocks, self._links = [], [], []
        elif self._links is not None and tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._links.append(href)
        elif self._direct is not None and "content-cell" in classes:
            self._in_body = (
                "mdl-typography--caption" not in classes
                and "mdl-typography--text-right" not in classes
            )
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
            if (
                self._direct is not None
                and self._blocks is not None
                and self._links is not None
            ):
                self.activities.append((self._direct, self._blocks, self._links))
            self._direct = None
            self._blocks = None
            self._links = None
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
        int(year),
        int(month),
        int(day),
        hour_value,
        int(minute),
        int(second),
        tzinfo=KST,
    ).isoformat()


def _gemini_conversation_id(links: list[str]) -> str | None:
    for link in links:
        parsed = urlparse(link)
        if parsed.hostname not in {"gemini.google.com", "bard.google.com"}:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if (
            len(parts) >= 2
            and parts[-2] == "app"
            and re.fullmatch(r"[A-Za-z0-9_-]+", parts[-1])
        ):
            return parts[-1]
    return None


def _parse_gemini(content: bytes, participant_id: str) -> dict[str, Any]:
    parser = _GeminiTakeoutParser()
    parser.feed(_decode_html(content))
    grouped: dict[str, list[tuple[int, str | None, str, str]]] = {}
    for index, (direct, blocks, links) in enumerate(parser.activities, start=1):
        timestamps = [_gemini_timestamp(value) for value in direct]
        timestamp = next((value for value in timestamps if value is not None), None)
        prompt = next(
            (value for value in direct if _gemini_timestamp(value) is None), ""
        )
        prompt = re.sub(r"\s*항목을 검색함\s*$", "", prompt).strip()
        response = "\n".join(blocks).strip()
        if prompt or response:
            session_id = _gemini_conversation_id(links) or f"takeout-{index}"
            grouped.setdefault(session_id, []).append(
                (index, timestamp, prompt, response)
            )
    sessions: list[dict[str, Any]] = []
    for session_id, activities in grouped.items():
        activities.sort(key=lambda item: (item[1] is None, item[1] or "", item[0]))
        turns: list[dict[str, Any]] = []
        for _, timestamp, prompt, response in activities:
            if prompt:
                turns.append(
                    {
                        "turnId": len(turns) + 1,
                        "role": "user",
                        "content": prompt,
                        "timestamp": timestamp,
                    }
                )
            if response:
                turns.append(
                    {
                        "turnId": len(turns) + 1,
                        "role": "assistant",
                        "content": response,
                        "timestamp": timestamp,
                    }
                )
        if turns:
            title = next((prompt for _, _, prompt, _ in activities if prompt), "")
            sessions.append(_session("gemini", session_id, title[:80], turns))
    sessions.sort(
        key=lambda session: (
            session["sessionMetadata"].get("startTime") is None,
            session["sessionMetadata"].get("startTime") or "",
        )
    )
    if not sessions:
        raise ValueError("Gemini 내 활동 HTML에서 대화 내역을 찾지 못했습니다.")
    return _summary(participant_id, "gemini", "gemini_takeout_html", sessions)


def _iso(value: object) -> str | None:
    try:
        return (
            datetime.fromtimestamp(float(value), tz=UTC).astimezone(KST).isoformat()
            if value
            else None
        )
    except (TypeError, ValueError, OSError):
        return None


def _iso_datetime(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(KST).isoformat()


def _summary(
    participant_id: str, platform: str, source_type: str, sessions: list[dict[str, Any]]
) -> dict[str, Any]:
    starts = [
        item["sessionMetadata"]["startTime"]
        for item in sessions
        if item["sessionMetadata"].get("startTime")
    ]
    ends = [
        item["sessionMetadata"]["endTime"]
        for item in sessions
        if item["sessionMetadata"].get("endTime")
    ]
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
                    "turnMetadata": (
                        {"modelSlug": metadata.get("model_slug")}
                        if metadata.get("model_slug")
                        else None
                    ),
                }
            )
        if turns:
            sessions.append(
                _session(
                    "chatgpt",
                    conversation.get("conversation_id")
                    or conversation.get("id")
                    or "unknown",
                    conversation.get("title") or "",
                    turns,
                )
            )
    return _summary(participant_id, "chatgpt", "chatgpt_export_json", sessions)


def _parse_claude(
    content: bytes, participant_id: str
) -> tuple[dict[str, Any], list[str]]:
    conversations = _decode_json(content)
    if not _is_claude_export(conversations):
        raise ValueError("올바른 Claude conversations.json 구조가 아닙니다.")
    sessions: list[dict[str, Any]] = []
    empty_messages = 0
    attachment_messages = 0
    for index, conversation in enumerate(conversations, start=1):
        turns: list[dict[str, Any]] = []
        for message in conversation.get("chat_messages", []):
            attachments = message.get("attachments") or []
            files = message.get("files") or []
            if attachments or files:
                attachment_messages += 1
            text = message.get("text")
            if not isinstance(text, str) or not text.strip():
                empty_messages += 1
                continue
            role = "user" if message.get("sender") == "human" else "assistant"
            metadata = {
                "messageUuid": message.get("uuid"),
                "parentMessageUuid": message.get("parent_message_uuid"),
            }
            turn_metadata = {
                key: value for key, value in metadata.items() if value
            } or None
            turns.append(
                {
                    "turnId": len(turns) + 1,
                    "role": role,
                    "content": text.strip(),
                    "contentFormat": "markdown" if role == "assistant" else "plainText",
                    "timestamp": _iso_datetime(message.get("created_at")),
                    "turnMetadata": turn_metadata,
                }
            )
        if turns:
            title = conversation.get("name")
            sessions.append(
                _session(
                    "claude",
                    conversation.get("uuid") or f"conversation-{index}",
                    title if isinstance(title, str) else "",
                    turns,
                )
            )
    if not sessions:
        raise ValueError("Claude conversations.json에서 대화 내역을 찾지 못했습니다.")
    warnings: list[str] = []
    if attachment_messages:
        warnings.append(
            f"Claude 첨부 파일이 포함된 메시지 {attachment_messages}개의 파일 본문은 대화문에 포함하지 않았습니다."
        )
    if empty_messages:
        warnings.append(
            f"Claude 본문이 비어 있는 메시지 {empty_messages}개를 제외했습니다."
        )
    return (
        _summary(participant_id, "claude", "claude_export_json", sessions),
        warnings,
    )


def _parse_grok(content: bytes, participant_id: str) -> dict[str, Any]:
    data = _decode_json(content)
    if not _is_grok_export(data):
        raise ValueError("올바른 Grok 대화 내보내기 구조가 아닙니다.")
    sessions: list[dict[str, Any]] = []
    for item in data.get("conversations", []) if isinstance(data, dict) else []:
        conversation = item.get("conversation") or {}
        turns: list[dict[str, Any]] = []
        responses = [
            row.get("response") or {}
            for row in item.get("responses", [])
            if isinstance(row, dict)
        ]
        for response in sorted(
            responses,
            key=lambda row: (
                (row.get("create_time") or {}).get("$date", {}).get("$numberLong", 0)
            ),
        ):
            value = ((response.get("create_time") or {}).get("$date", {}) or {}).get(
                "$numberLong"
            )
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
                        {
                            "model": response.get("model"),
                            "errors": metadata.get("stream_errors"),
                        }
                        if role == "assistant"
                        else None
                    ),
                }
            )
        if turns:
            sessions.append(
                _session(
                    "grok",
                    conversation.get("id") or "unknown",
                    conversation.get("title") or "",
                    turns,
                )
            )
    return _summary(participant_id, "grok", "grok_export_json", sessions)


def _session(
    platform: str, original_id: str, title: str, turns: list[dict[str, Any]]
) -> dict[str, Any]:
    timestamps = [turn["timestamp"] for turn in turns if turn.get("timestamp")]
    start, end = (timestamps[0], timestamps[-1]) if timestamps else (None, None)
    duration = (
        int(
            (
                datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds()
        )
        if start and end
        else None
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
