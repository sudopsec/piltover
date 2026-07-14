from piltover.app.utils.formatable_text_with_entities import (
    FormatableTextWithEntities,
    utf16_slice,
)
from piltover.tl import MessageEntityBold, MessageEntityMention


def _entity_text(text: str, entity: dict[str, str | int]) -> str:
    return utf16_slice(text, entity["offset"], entity["length"])


def test_entities_use_utf16_offsets_for_emoji() -> None:
    formatted, entities = FormatableTextWithEntities("Hello 👋 **world**").format()
    assert formatted == "Hello 👋 world"
    assert len(entities) == 1
    assert entities[0]["_"] == MessageEntityBold.tlid()
    assert _entity_text(formatted, entities[0]) == "world"


def test_entities_with_placeholders_and_mention() -> None:
    template = "Here it is: {name} <u>@{username}</u>."
    formatted, entities = FormatableTextWithEntities(template).format(
        name="TestBot",
        username="mybot",
    )
    assert formatted == "Here it is: TestBot @mybot."
    assert len(entities) == 1
    assert entities[0]["_"] == MessageEntityMention.tlid()
    assert _entity_text(formatted, entities[0]) == "@mybot"


def test_entities_with_bold_label_and_placeholder() -> None:
    formatted, entities = FormatableTextWithEntities("**Name**: {name}").format(name="Bot")
    assert formatted == "Name: Bot"
    assert len(entities) == 1
    assert entities[0]["_"] == MessageEntityBold.tlid()
    assert _entity_text(formatted, entities[0]) == "Name"


def test_botfather_edit_info_entities_cover_labels() -> None:
    template = FormatableTextWithEntities("""
Edit <u>@{username}</u> info.

**Name**: {name}
**About**: {about}
""".strip())
    formatted, entities = template.format(
        username="demo",
        name="Demo Bot",
        about="About text",
    )
    bold_entities = [e for e in entities if e["_"] == MessageEntityBold.tlid()]
    assert _entity_text(formatted, bold_entities[0]) == "Name"
    assert _entity_text(formatted, bold_entities[1]) == "About"
    assert _entity_text(formatted, next(e for e in entities if e["_"] == MessageEntityMention.tlid())) == "@demo"