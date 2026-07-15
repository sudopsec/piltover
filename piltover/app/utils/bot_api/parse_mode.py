from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape

from piltover.exceptions import ErrorRpc
from piltover.tl import (
    MessageEntityBlockquote, MessageEntityBold, MessageEntityCode, MessageEntityCustomEmoji,
    MessageEntityItalic, MessageEntityMentionName, MessageEntityPre, MessageEntitySpoiler,
    MessageEntityStrike, MessageEntityTextUrl, MessageEntityUnderline,
)
from piltover.tl.base import MessageEntity as TLMessageEntityBase

_HTML_TAG_RE = re.compile(
    r"<\s*(/?)\s*([a-zA-Z0-9]+)([^>]*)>|&(#\d+|#x[\da-fA-F]+|[a-zA-Z]+);",
)
_HTML_ATTR_RE = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"')

_BOLD_TAGS = frozenset({"b", "strong"})
_ITALIC_TAGS = frozenset({"i", "em"})
_UNDERLINE_TAGS = frozenset({"u", "ins"})
_STRIKE_TAGS = frozenset({"s", "strike", "del"})
_SPOILER_TAGS = frozenset({"tg-spoiler", "span"})
_CODE_TAGS = frozenset({"code"})
_PRE_TAGS = frozenset({"pre"})
_LINK_TAGS = frozenset({"a"})
_BLOCKQUOTE_TAGS = frozenset({"blockquote"})
_EMOJI_TAGS = frozenset({"tg-emoji"})


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16le")) // 2


@dataclass
class _OpenTag:
    tag: str
    entity_type: type[TLMessageEntityBase]
    start_offset: int
    extra: dict[str, str] = field(default_factory=dict)


def _parse_html_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _HTML_ATTR_RE.finditer(raw):
        attrs[match.group(1).lower()] = unescape(match.group(2))
    return attrs


def _entity_from_open_tag(tag: str, attrs: dict[str, str]) -> type[TLMessageEntityBase] | None:
    lowered = tag.lower()
    if lowered in _BOLD_TAGS:
        return MessageEntityBold
    if lowered in _ITALIC_TAGS:
        return MessageEntityItalic
    if lowered in _UNDERLINE_TAGS:
        return MessageEntityUnderline
    if lowered in _STRIKE_TAGS:
        return MessageEntityStrike
    if lowered in _CODE_TAGS:
        return MessageEntityCode
    if lowered in _PRE_TAGS:
        return MessageEntityPre
    if lowered in _BLOCKQUOTE_TAGS:
        return MessageEntityBlockquote
    if lowered in _LINK_TAGS:
        return MessageEntityTextUrl
    if lowered in _EMOJI_TAGS:
        return MessageEntityCustomEmoji
    if lowered in _SPOILER_TAGS:
        if lowered == "tg-spoiler" or attrs.get("class") == "tg-spoiler":
            return MessageEntitySpoiler
    return None


def _append_entity(
        entities: list[TLMessageEntityBase],
        entity_type: type[TLMessageEntityBase],
        start_offset: int,
        end_offset: int,
        **kwargs: object,
) -> None:
    length = end_offset - start_offset
    if length <= 0:
        return
    entities.append(entity_type(offset=start_offset, length=length, **kwargs))


def parse_html(text: str) -> tuple[str, list[TLMessageEntityBase]]:
    result: list[str] = []
    entities: list[TLMessageEntityBase] = []
    stack: list[_OpenTag] = []
    offset = 0
    pos = 0

    def append_plain(chunk: str) -> None:
        nonlocal offset
        if not chunk:
            return
        result.append(chunk)
        offset += utf16_len(chunk)

    while pos < len(text):
        match = _HTML_TAG_RE.search(text, pos)
        if match is None:
            append_plain(text[pos:])
            break

        append_plain(text[pos:match.start()])
        closing, tag_name, attrs_raw, entity_ref = match.groups()
        pos = match.end()

        if entity_ref is not None:
            append_plain(unescape(f"&{entity_ref};"))
            continue

        if not tag_name:
            append_plain(match.group(0))
            continue

        tag = tag_name.lower()
        attrs = _parse_html_attrs(attrs_raw or "")

        if closing:
            for index in range(len(stack) - 1, -1, -1):
                open_tag = stack[index]
                if open_tag.tag != tag:
                    continue
                stack.pop(index)
                kwargs: dict[str, object] = {}
                if open_tag.entity_type is MessageEntityTextUrl:
                    kwargs["url"] = open_tag.extra.get("href", "")
                elif open_tag.entity_type is MessageEntityPre:
                    if language := open_tag.extra.get("language"):
                        kwargs["language"] = language
                elif open_tag.entity_type is MessageEntityCustomEmoji:
                    kwargs["document_id"] = int(open_tag.extra.get("emoji-id", "0"))
                _append_entity(entities, open_tag.entity_type, open_tag.start_offset, offset, **kwargs)
                break
            continue

        entity_type = _entity_from_open_tag(tag, attrs)
        if entity_type is None:
            append_plain(match.group(0))
            continue

        extra = dict(attrs)
        if entity_type is MessageEntityTextUrl:
            href = attrs.get("href")
            if not href:
                raise ErrorRpc(error_code=400, error_message="Bad Request: can't parse entities: empty href")
            extra["href"] = href
        elif entity_type is MessageEntityCustomEmoji:
            emoji_id = attrs.get("emoji-id")
            if not emoji_id or not emoji_id.isdigit():
                raise ErrorRpc(error_code=400, error_message="Bad Request: can't parse entities: invalid emoji-id")
            extra["emoji-id"] = emoji_id

        stack.append(_OpenTag(tag=tag, entity_type=entity_type, start_offset=offset, extra=extra))

    plain = "".join(result)
    return plain, entities


_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_PRE_RE = re.compile(r"```(?:([a-zA-Z0-9_+-]+)\n)?([\s\S]*?)```")


def _parse_markdown_delimited(
        text: str,
        pattern: re.Pattern[str],
        entity_type: type[TLMessageEntityBase],
        *,
        link: bool = False,
        language_group: int | None = None,
        content_group: int = 1,
) -> tuple[str, list[TLMessageEntityBase]]:
    entities: list[TLMessageEntityBase] = []
    plain_parts: list[str] = []
    offset = 0
    last = 0

    for match in pattern.finditer(text):
        plain_parts.append(text[last:match.start()])
        offset += utf16_len(text[last:match.start()])

        if link:
            visible = match.group(1)
            url = match.group(2)
            plain_parts.append(visible)
            _append_entity(entities, entity_type, offset, offset + utf16_len(visible), url=url)
            offset += utf16_len(visible)
        elif language_group is not None:
            language = match.group(language_group) or ""
            content = match.group(content_group)
            plain_parts.append(content)
            kwargs = {"language": language} if language else {}
            _append_entity(entities, entity_type, offset, offset + utf16_len(content), **kwargs)
            offset += utf16_len(content)
        else:
            content = match.group(content_group) or match.group(2) or ""
            if not content and match.lastindex:
                for group_index in range(1, match.lastindex + 1):
                    if match.group(group_index):
                        content = match.group(group_index)
                        break
            plain_parts.append(content)
            _append_entity(entities, entity_type, offset, offset + utf16_len(content))
            offset += utf16_len(content)

        last = match.end()

    plain_parts.append(text[last:])
    return "".join(plain_parts), entities


def parse_markdown(text: str) -> tuple[str, list[TLMessageEntityBase]]:
    entities: list[TLMessageEntityBase] = []

    text, links = _parse_markdown_delimited(text, _MD_LINK_RE, MessageEntityTextUrl, link=True)
    entities.extend(links)

    text, pres = _parse_markdown_delimited(
        text, _MD_PRE_RE, MessageEntityPre, language_group=1, content_group=2,
    )
    entities.extend(pres)

    text, codes = _parse_markdown_delimited(text, _MD_CODE_RE, MessageEntityCode)
    entities.extend(codes)

    text, bolds = _parse_markdown_delimited(text, _MD_BOLD_RE, MessageEntityBold)
    entities.extend(bolds)

    text, italics = _parse_markdown_delimited(text, _MD_ITALIC_RE, MessageEntityItalic)
    entities.extend(italics)

    return text, entities


_MD2_ESCAPE_RE = re.compile(r"\\([_*\[\]()~`>#+\-=|{}.!])")
_MD2_BOLD_RE = re.compile(r"\*([^*\n]+)\*")
_MD2_ITALIC_RE = re.compile(r"_([^_\n]+)_")
_MD2_UNDERLINE_RE = re.compile(r"__([^_\n]+)__")
_MD2_STRIKE_RE = re.compile(r"~([^~\n]+)~")
_MD2_SPOILER_RE = re.compile(r"\|\|([^|\n]+)\|\|")
_MD2_CODE_RE = re.compile(r"`([^`\n]+)`")
_MD2_PRE_RE = re.compile(r"```(?:([a-zA-Z0-9_+-]+))?[\n ]([\s\S]*?)```")
_MD2_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def parse_markdown_v2(text: str) -> tuple[str, list[TLMessageEntityBase]]:
    text = _MD2_ESCAPE_RE.sub(r"\1", text)
    entities: list[TLMessageEntityBase] = []

    for pattern, entity_type, kwargs in (
        (_MD2_LINK_RE, MessageEntityTextUrl, {"link": True}),
        (_MD2_PRE_RE, MessageEntityPre, {"language_group": 1, "content_group": 2}),
        (_MD2_CODE_RE, MessageEntityCode, {}),
        (_MD2_UNDERLINE_RE, MessageEntityUnderline, {}),
        (_MD2_BOLD_RE, MessageEntityBold, {}),
        (_MD2_ITALIC_RE, MessageEntityItalic, {}),
        (_MD2_STRIKE_RE, MessageEntityStrike, {}),
        (_MD2_SPOILER_RE, MessageEntitySpoiler, {}),
    ):
        if kwargs.get("link"):
            text, parsed = _parse_markdown_delimited(text, pattern, entity_type, link=True)
        elif "language_group" in kwargs:
            text, parsed = _parse_markdown_delimited(
                text, pattern, entity_type,
                language_group=kwargs["language_group"],
                content_group=kwargs["content_group"],
            )
        else:
            text, parsed = _parse_markdown_delimited(text, pattern, entity_type)
        entities.extend(parsed)

    return text, entities


def parse_text_mode(text: str, parse_mode: str) -> tuple[str, list[TLMessageEntityBase]]:
    normalized = parse_mode.strip().lower().replace(" ", "")
    if normalized in {"html"}:
        return parse_html(text)
    if normalized in {"markdown"}:
        return parse_markdown(text)
    if normalized in {"markdownv2", "markdown_v2"}:
        return parse_markdown_v2(text)
    raise ErrorRpc(error_code=400, error_message=f"Bad Request: unsupported parse_mode {parse_mode}")