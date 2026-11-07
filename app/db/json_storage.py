from __future__ import annotations

import gzip
import hashlib
import io
import json
from typing import Any

from bson import BSON, Binary

INLINE_LIMIT = 8 * 1024 * 1024
COMPRESSED_LIMIT = 14 * 1024 * 1024
DECODE_LIMIT = 128 * 1024 * 1024
ENCODING = 'gzip-json-v1'
HEADER_KEYS = {'status', 'parser_version', 'warnings', 'source_error_code',
               'participantId', 'platform', 'sourceType', 'schemaVersion', 'parsedAt', 'summary'}


def encode_json_storage(value: dict[str, Any]) -> dict[str, Any]:
    if len(BSON.encode({'payload': value})) <= INLINE_LIMIT:
        return value
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > DECODE_LIMIT:
        raise ValueError('Transcript JSON exceeds the supported lossless storage limit')
    stored = {key: content for key, content in value.items() if key in HEADER_KEYS}
    stored['storageEncoding'] = ENCODING
    stored['compressedJson'] = Binary(gzip.compress(raw, mtime=0))
    stored['jsonSha256'] = hashlib.sha256(raw).hexdigest()
    if len(BSON.encode({'payload': stored})) > COMPRESSED_LIMIT:
        raise ValueError('Transcript JSON requires external large-object storage')
    return stored


def decode_json_storage(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None or 'storageEncoding' not in value:
        return value
    if value['storageEncoding'] != ENCODING:
        raise ValueError('Unsupported transcript JSON storage encoding')
    with gzip.GzipFile(fileobj=io.BytesIO(bytes(value['compressedJson']))) as stream:
        raw = stream.read(DECODE_LIMIT + 1)
    if len(raw) > DECODE_LIMIT or hashlib.sha256(raw).hexdigest() != value['jsonSha256']:
        raise ValueError('Transcript JSON storage integrity check failed')
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError('Transcript JSON storage payload must be an object')
    return decoded