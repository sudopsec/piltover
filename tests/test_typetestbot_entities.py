import pytest

from piltover.app.bot_handlers.typetestbot.callback_handler import typetestbot_callback_query_handler
from piltover.app.bot_handlers.typetestbot.catalog.builders import entity_for_substring
from piltover.app.bot_handlers.typetestbot.catalog.pages import _paged_menu
from piltover.app.bot_handlers.typetestbot.common import NAV_NOOP_CALLBACK
from piltover.app.bot_handlers.typetestbot.catalog.registry import CATALOG_HANDLERS
from piltover.app.utils.formatable_text_with_entities import utf16_slice
from piltover.tl import MessageEntityBold, MessageEntityMention


def test_catalog_index_keyboard_has_hub_on_separate_row() -> None:
    from piltover.app.bot_handlers.typetestbot.catalog.pages import catalog_index_keyboard

    keyboard = catalog_index_keyboard()
    assert len(keyboard.rows[-1].buttons) == 1
    assert keyboard.rows[-1].buttons[0].text == "← Hub"
    assert "Bot actions" in keyboard.rows[1].buttons[1].text


def test_buttons_menu_keyboard_has_padding_and_hub_on_separate_row() -> None:
    from piltover.app.bot_handlers.typetestbot.buttons import buttons_menu_keyboard
    from piltover.app.bot_handlers.typetestbot.common import NAV_NOOP_CALLBACK

    keyboard = buttons_menu_keyboard()
    assert len(keyboard.rows[-1].buttons) == 1
    assert keyboard.rows[-1].buttons[0].text == "← Hub"

    last_item_row = keyboard.rows[-2]
    assert len(last_item_row.buttons) == 2
    assert last_item_row.buttons[1].text == ""
    assert last_item_row.buttons[1].data == NAV_NOOP_CALLBACK


def test_hub_keyboard_uses_separate_rows() -> None:
    from piltover.app.bot_handlers.typetestbot.common import hub_keyboard

    keyboard = hub_keyboard()
    assert len(keyboard.rows) == 2
    assert keyboard.rows[0].buttons[0].text == "🔘 Buttons"
    assert keyboard.rows[1].buttons[0].text == "📋 Catalog"


def test_paged_menu_odd_chunk_gets_padding_before_nav() -> None:
    from piltover.app.bot_handlers.typetestbot.catalog.registry import _SERVICE_SPECIMENS

    items = [(s.key, s.label) for s in _SERVICE_SPECIMENS]
    last_page = (len(items) - 1) // 14
    chunk_len = len(items) - last_page * 14
    assert chunk_len % 2 == 1

    keyboard = _paged_menu(items, page=last_page, category="service")
    nav_row = keyboard.rows[-3]
    catalog_row = keyboard.rows[-2]
    hub_row = keyboard.rows[-1]

    assert len(nav_row.buttons) == 1
    assert nav_row.buttons[0].text == "◀ Prev"
    assert len(catalog_row.buttons) == 1
    assert catalog_row.buttons[0].text == "← Catalog"
    assert len(hub_row.buttons) == 1
    assert hub_row.buttons[0].text == "← Hub"


def test_paged_menu_shows_only_available_nav_buttons() -> None:
    from piltover.app.bot_handlers.typetestbot.catalog.registry import _ENTITY_HANDLERS

    items = [(k, k.decode().rsplit(":", 1)[-1]) for k in sorted(_ENTITY_HANDLERS)]
    assert len(items) > 14

    keyboard = _paged_menu(items, page=0, category="entities")
    nav_buttons = keyboard.rows[-3].buttons
    assert len(nav_buttons) == 1
    assert nav_buttons[0].text == "Next ▶"

    last_page = (len(items) - 1) // 14
    keyboard_last = _paged_menu(items, page=last_page, category="entities")
    nav_buttons_last = keyboard_last.rows[-3].buttons
    assert len(nav_buttons_last) == 1
    assert nav_buttons_last[0].text == "◀ Prev"


def test_paged_menu_single_page_has_no_nav_buttons() -> None:
    from piltover.app.bot_handlers.typetestbot.catalog.registry import REGULAR_SPECIMENS

    items = [(k, l) for k, l, _ in REGULAR_SPECIMENS]
    assert len(items) <= 14

    keyboard = _paged_menu(items, page=0, category="regular")
    assert keyboard.rows[-2].buttons[0].text == "← Catalog"
    assert keyboard.rows[-1].buttons[0].text == "← Hub"
    assert all(
        btn.text not in {"◀ Prev", "Next ▶"}
        for row in keyboard.rows[:-2]
        for btn in row.buttons
    )


@pytest.mark.asyncio
async def test_typetestbot_action_callback_edits_menu_message() -> None:
    from piltover.db.models import MessageRef, Peer, User
    from tests.client import TestClient

    async with TestClient(phone_number="123456789") as client:
        from piltover.app.bot_handlers.typetestbot.catalog.pages import page_category

        user = await User.get(phone_number=client.phone_number)
        bot = await client.get_users("typetestbot")
        peer = await Peer.get(owner_id=user.id, user_id=bot.id)
        menu = await page_category(peer, "actions", 0)
        before_count = await MessageRef.filter(peer=peer).count()

        answer = await typetestbot_callback_query_handler(peer, menu, b"cat:act:typing")
        assert answer is not None
        assert answer.message is None

        await menu.content.refresh_from_db()
        assert "Typing indicator sent" in menu.content.message
        assert await MessageRef.filter(peer=peer).count() == before_count


@pytest.mark.asyncio
async def test_typetestbot_incoming_call_action() -> None:
    from piltover.app.bot_handlers.typetestbot.catalog.builders import send_incoming_call
    from piltover.db.models import Peer, User
    from tests.client import TestClient

    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        bot = await client.get_users("typetestbot")
        peer = await Peer.get(owner_id=user.id, user_id=bot.id)
        await send_incoming_call(peer)


@pytest.mark.asyncio
async def test_typetestbot_noop_callback_returns_empty_answer() -> None:
    from piltover.db.models import MessageRef, Peer, User
    from tests.client import TestClient

    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        bot = await client.get_users("typetestbot")
        peer = await Peer.get(owner_id=user.id, user_id=bot.id)
        message = await MessageRef.filter(peer=peer).order_by("-id").first()

        answer = await typetestbot_callback_query_handler(peer, message, NAV_NOOP_CALLBACK)
        assert answer is not None
        assert answer.cache_time == 0
        assert answer.message is None


def test_entity_for_substring_uses_utf16_offset_after_prefix() -> None:
    from piltover.app.bot_handlers.typetestbot.catalog.builders import entity_at

    prefix = "[entity/bold] "
    message = prefix + "bold"
    entity = entity_at(message, len(prefix), "bold", MessageEntityBold)
    assert utf16_slice(message, entity["offset"], entity["length"]) == "bold"
    assert entity["offset"] == len(prefix)


def test_entity_for_substring_start_skips_name_inside_prefix() -> None:
    message = "[entity/bold] bold"
    wrong = entity_for_substring(message, "bold", MessageEntityBold)
    right = entity_for_substring(message, "bold", MessageEntityBold, start=len("[entity/bold] "))
    assert utf16_slice(message, wrong["offset"], wrong["length"]) == "bold"
    assert wrong["offset"] < right["offset"]
    assert right["offset"] == len("[entity/bold] ")


@pytest.mark.asyncio
async def test_typetestbot_entity_bold_specimen() -> None:
    from piltover.db.models import Peer, User
    from tests.client import TestClient

    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        bot = await client.get_users("typetestbot")
        peer = await Peer.get(owner_id=user.id, user_id=bot.id)

        message = await CATALOG_HANDLERS[b"cat:ent:bold"](peer)
        assert message.content.message == "[entity/bold] bold"
        assert message.content.entities is not None
        assert len(message.content.entities) == 1
        entity = message.content.entities[0]
        assert entity["_"] == MessageEntityBold.tlid()
        assert utf16_slice(message.content.message, entity["offset"], entity["length"]) == "bold"
        assert entity["offset"] == len("[entity/bold] ")


@pytest.mark.asyncio
async def test_typetestbot_entity_mention_specimen() -> None:
    from piltover.db.models import Peer, User
    from tests.client import TestClient

    async with TestClient(phone_number="123456789") as client:
        user = await User.get(phone_number=client.phone_number)
        bot = await client.get_users("typetestbot")
        peer = await Peer.get(owner_id=user.id, user_id=bot.id)

        message = await CATALOG_HANDLERS[b"cat:ent:mention"](peer)
        entity = message.content.entities[0]
        assert entity["_"] == MessageEntityMention.tlid()
        assert utf16_slice(message.content.message, entity["offset"], entity["length"]) == "@user"