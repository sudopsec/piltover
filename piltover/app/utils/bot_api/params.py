from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote_plus

_BRACKET_KEY_RE = re.compile(r"^([^[]+)(?:\[([^\]]+)\])+$")

_JSON_OBJECT_FIELDS = frozenset({
    "reply_parameters",
    "reply_markup",
    "reply_parameters_quote",
    "quote",
})
_JSON_ARRAY_FIELDS = frozenset({
    "entities",
    "caption_entities",
    "commands",
    "allowed_updates",
    "quote_entities",
})


def _coerce_segment(segment: str) -> str | int:
    if segment.isdigit() or (segment.startswith("-") and segment[1:].isdigit()):
        try:
            return int(segment)
        except ValueError:
            return segment
    return segment


def _ensure_container(parent: dict[str, Any] | list[Any], key: str | int, next_key: str | int) -> dict[str, Any] | list[Any]:
    if isinstance(parent, list):
        try:
            index = key if isinstance(key, int) else int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid nested parameter index") from exc
        while len(parent) <= index:
            parent.append(None)
        if parent[index] is None:
            parent[index] = [] if isinstance(next_key, int) else {}
        return parent[index]

    if key not in parent or not isinstance(parent[key], (dict, list)):
        parent[key] = [] if isinstance(next_key, int) else {}
    return parent[key]


def _set_nested_value(root: dict[str, Any] | list[Any], path: list[str], value: Any) -> None:
    if not path:
        return

    current: dict[str, Any] | list[Any] = root
    for index, segment in enumerate(path[:-1]):
        key = _coerce_segment(segment)
        next_key = _coerce_segment(path[index + 1])
        current = _ensure_container(current, key, next_key)

    last = _coerce_segment(path[-1])
    coerced = coerce_param_value(value)
    if isinstance(current, list):
        try:
            index = last if isinstance(last, int) else int(last)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid nested parameter index") from exc
        while len(current) <= index:
            current.append(None)
        current[index] = coerced
    else:
        current[last] = coerced


def _split_bracket_key(key: str) -> tuple[str, list[str]] | None:
    match = _BRACKET_KEY_RE.match(key)
    if match is None:
        return None
    base = match.group(1)
    segments = re.findall(r"\[([^\]]+)\]", key)
    return base, segments


def normalize_nested_params(params: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for key, value in params.items():
        parsed = _split_bracket_key(key)
        if parsed is None:
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = {**result[key], **value}
            else:
                result[key] = value
            continue

        base, path = parsed
        if base not in result or not isinstance(result[base], (dict, list)):
            first_key = _coerce_segment(path[0])
            result[base] = [] if isinstance(first_key, int) else {}
        _set_nested_value(result[base], path, value)

    return result


def coerce_param_value(value: Any) -> Any:
    if isinstance(value, list):
        if len(value) == 1:
            return coerce_param_value(value[0])
        return [coerce_param_value(item) for item in value]
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if not stripped:
        return value

    if stripped[0] in "{[":
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    lowered = stripped.lower()
    if lowered in ("true", "false"):
        return lowered == "true"

    if stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit()):
        try:
            return int(stripped)
        except ValueError:
            return value

    return value


def parse_query_value(value: str) -> Any:
    return coerce_param_value(unquote_plus(value))


def parse_json_object(value: Any, *, field_name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be a valid JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field_name} must be a JSON object")
        return parsed
    raise ValueError(f"{field_name} must be a JSON object")


def parse_json_array(value: Any, *, field_name: str) -> list[Any] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be a valid JSON array") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{field_name} must be a JSON array")
        return parsed
    raise ValueError(f"{field_name} must be a JSON array")


def finalize_bot_api_params(params: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_nested_params(params)

    for key, value in list(normalized.items()):
        coerced = coerce_param_value(value)
        if key in _JSON_OBJECT_FIELDS and coerced is not None:
            coerced = parse_json_object(coerced, field_name=key)
        elif key in _JSON_ARRAY_FIELDS and coerced is not None:
            coerced = parse_json_array(coerced, field_name=key)
        normalized[key] = coerced

    return normalized