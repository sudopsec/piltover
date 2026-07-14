from __future__ import annotations

from datetime import datetime, UTC
from time import time

import piltover.app.utils.updates_manager as upd
from piltover.app.utils.formatable_text_with_entities import FormatableTextWithEntities
from piltover.db.enums import MessageType
from piltover.db.models import MessageMention, MessageRef, Peer
from piltover.session import SessionManager
from piltover.tl import (
    KeyboardButtonCallback,
    KeyboardButtonRow,
    MessageMediaEmpty,
    ReplyInlineMarkup,
    UpdateServiceNotification,
    objects,
)
from piltover.tl.base import MessageActionInst, ReplyMarkup
from piltover.app.utils.updates_manager import UpdatesWithDefaults


async def send_bot_message(
        peer: Peer, text: str, keyboard: ReplyMarkup | None = None, **content_kwargs,
) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.user, opposite=False,
        message=text,
        reply_markup=keyboard.write() if keyboard else None,
        **content_kwargs,
    )
    return messages[peer]


async def send_service_message(
        peer: Peer, action: MessageActionInst,
        msg_type: MessageType = MessageType.SERVICE_PIN_MESSAGE,
        author: int | None = None,
) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, author or peer.user_id, opposite=False,
        type=msg_type,
        extra_info=action.write(),
    )
    return messages[peer]


async def inject_as_user(peer: Peer, **content_kwargs) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.owner_id, opposite=False, **content_kwargs,
    )
    user_message = messages[peer]
    await upd.send_message(peer.owner_id, {peer: user_message}, False)
    return user_message


async def inject_service_as_user(
        peer: Peer, action: MessageActionInst,
        msg_type: MessageType = MessageType.SERVICE_PIN_MESSAGE,
) -> MessageRef:
    messages = await MessageRef.create_for_peer(
        peer, peer.owner_id, opposite=False,
        type=msg_type,
        extra_info=action.write(),
    )
    user_message = messages[peer]
    await upd.send_message(peer.owner_id, {peer: user_message}, False)
    return user_message


async def send_service_notification(
        peer: Peer, *, popup: bool = True, message: str | None = None,
) -> None:
    text, entity_dicts = FormatableTextWithEntities(
        message or "📢 **Service notification** from @typetestbot\nTap OK to dismiss.",
    ).format()
    entities = []
    for entity in entity_dicts:
        tl_id = entity.pop("_")
        entities.append(objects[tl_id](**entity))
        entity["_"] = tl_id

    await SessionManager.send(
        UpdatesWithDefaults(updates=[
            UpdateServiceNotification(
                popup=popup,
                inbox_date=int(time()) if popup else None,
                type_=f"TYPETEST_{int(time())}",
                message=text,
                media=MessageMediaEmpty(),
                entities=entities,
            ),
        ]),
        peer.owner_id,
    )


async def mark_mentioned(peer: Peer, message: MessageRef) -> None:
    await MessageMention.get_or_create(user_id=peer.owner_id, message_id=message.content_id)


async def mark_pinned(message: MessageRef) -> None:
    message.pinned = True
    await message.save(update_fields=["pinned"])


async def mark_edit_hide(message: MessageRef) -> None:
    message.content.edit_hide = True
    message.content.edit_date = datetime.now(UTC)
    message.content.version += 1
    await message.content.save(update_fields=["edit_hide", "edit_date", "version"])


async def mark_from_scheduled(message: MessageRef) -> None:
    message.content.scheduled_date = datetime.now(UTC)
    message.content.version += 1
    await message.content.save(update_fields=["scheduled_date", "version"])


def _menu_rows(items: list[tuple[str, bytes]]) -> ReplyInlineMarkup:
    rows: list[KeyboardButtonRow] = []
    for idx, (label, data) in enumerate(items):
        if idx % 2 == 0:
            rows.append(KeyboardButtonRow(buttons=[]))
        rows[-1].buttons.append(KeyboardButtonCallback(text=label, data=data))
    return ReplyInlineMarkup(rows=rows)


def hub_keyboard() -> ReplyInlineMarkup:
    return ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🔘 Buttons", data=b"page:buttons"),
            KeyboardButtonCallback(text="📋 Catalog", data=b"page:catalog"),
        ]),
    ])


HUB_TEXT = (
    "🧪 Type Test Bot\n\n"
    "Pick a sandbox page:\n"
    "• Buttons — keyboards & edge cases (Buy without invoice, 2FA callback…)\n"
    "• Catalog — full list of all message types (possible & impossible)\n\n"
    "/buttons /catalog — open a page directly."
)

BUTTONS_PAGE_TEXT = (
    "🔘 Buttons page\n\n"
    "Tap a demo or use commands: /inline /buy_plain /password …\n"
    "/start — back to hub"
)

