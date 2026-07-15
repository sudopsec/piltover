from __future__ import annotations

import random

from piltover.app.bot_handlers.adminbot import pages_server
from piltover.app.utils.server_settings import toggle_server_setting
from piltover.app.utils.system_notifications import (
    broadcast_official_notification_message,
    broadcast_service_notification,
    send_official_notification_message,
    send_service_notification,
)
from piltover.config import APP_CONFIG
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, MessageRef, Peer, User, Username
from piltover.tl.types.messages import BotCallbackAnswer

_CFG_FIELD_MAP = {
    "reports": "reports_enabled",
    "bots": "bot_creation_enabled",
    "groups": "group_creation_enabled",
    "channels": "channel_creation_enabled",
    "calls": "phone_calls_enabled",
    "verifybot": "verifybot_enabled",
    "stars": "stars_bot_enabled",
}


async def _clear_system_input_state(admin_user_id: int) -> None:
    await AdminBotUserState.filter(
        user_id=admin_user_id,
        state__in=[AdminBotState.WAIT_SYSTEM_TARGET, AdminBotState.WAIT_SYSTEM_TEXT],
    ).delete()


async def _resolve_menu(menu_id: int | None) -> MessageRef | None:
    if menu_id is None:
        return None
    return await MessageRef.get_or_none(id=menu_id).select_related("content", "peer")


def _encode_state(mode: str, menu_id: int, *extra: str | int) -> bytes:
    parts = [mode, *[str(part) for part in extra], str(menu_id)]
    return ":".join(parts).encode()


def _parse_state(data: bytes) -> tuple[str, list[str], int | None]:
    parts = data.decode().split(":")
    menu_id = None
    if parts and parts[-1].isdigit():
        menu_id = int(parts[-1])
        parts = parts[:-1]
    mode = parts[0] if parts else ""
    return mode, parts[1:], menu_id


_BROADCAST_TARGET_TOKENS = frozenset({"all", "*", "everyone", "все", "всем"})


def is_broadcast_target(query: str) -> bool:
    return query.strip().lower() in _BROADCAST_TARGET_TOKENS


async def resolve_notification_target(query: str) -> User | None:
    text = query.strip()
    if not text:
        return None
    if text.startswith("@"):
        text = text[1:]
    if text.isdigit():
        user = await User.get_or_none(id=int(text), deleted=False)
        if user is not None:
            return user
    if text.startswith("+") or text.replace(" ", "").isdigit():
        phone = "".join(ch for ch in text if ch.isdigit())
        if len(phone) >= 5:
            return await User.get_or_none(phone_number=phone, deleted=False)
    row = await Username.get_or_none(username__iexact=text).select_related("user")
    if row is None or row.user_id is None or row.user.deleted:
        return None
    return row.user


async def toggle_config_action(peer: Peer, menu: MessageRef, key: str) -> BotCallbackAnswer:
    field = _CFG_FIELD_MAP.get(key)
    if field is None:
        return BotCallbackAnswer(message="Неизвестная настройка.", alert=True, cache_time=0)
    if await toggle_server_setting(field) is None:
        return BotCallbackAnswer(message="Не удалось переключить.", alert=True, cache_time=0)
    await pages_server.page_server_config(peer, menu)
    return BotCallbackAnswer(message="Настройка обновлена.", cache_time=0)


def _notify_title() -> str:
    return f"📨 Сообщение от +42777 (@{APP_CONFIG.system_user_username})"


def _srv_title(*, popup: bool) -> str:
    style = "popup" if popup else "тихое"
    return f"🔔 Service notification ({style})"


async def begin_notify_target(peer: Peer, menu: MessageRef, *, admin_user_id: int) -> BotCallbackAnswer:
    await AdminBotUserState.set_state(
        admin_user_id, AdminBotState.WAIT_SYSTEM_TARGET, _encode_state("notify", menu.id),
    )
    await pages_server.page_system_target_prompt(
        peer, menu,
        title=_notify_title(),
        back_data=b"adm:server",
        everyone_data=b"adm:notify:all",
    )
    return BotCallbackAnswer(message="Укажите получателя или нажмите «Всем».", cache_time=0)


async def begin_notify_broadcast(peer: Peer, menu: MessageRef, *, admin_user_id: int) -> BotCallbackAnswer:
    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_SYSTEM_TEXT,
        _encode_state("notify", menu.id, "all"),
    )
    await pages_server.page_system_text_prompt(
        peer, menu,
        title=_notify_title(),
        target_label="Все пользователи",
        back_data=b"adm:notify",
    )
    return BotCallbackAnswer(message="Отправьте текст сообщения.", cache_time=0)


async def begin_srvnotif_menu(peer: Peer, menu: MessageRef, *, admin_user_id: int) -> BotCallbackAnswer:
    from piltover.app.bot_handlers.typetestbot.common import edit_bot_message
    from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, ReplyInlineMarkup

    await _clear_system_input_state(admin_user_id)
    text = (
        "🔔 Service notification\n\n"
        "Выберите тип доставки, затем получателя или рассылку всем."
    )
    keyboard = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Popup", data=b"adm:srvnotif:1"),
            KeyboardButtonCallback(text="Тихое", data=b"adm:srvnotif:0"),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Назад", data=b"adm:server"),
        ]),
    ])
    await edit_bot_message(menu, peer, text, keyboard)
    return BotCallbackAnswer(cache_time=0)


async def begin_srvnotif_target(
        peer: Peer, menu: MessageRef, *, popup: bool, admin_user_id: int,
) -> BotCallbackAnswer:
    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_SYSTEM_TARGET,
        _encode_state("srv", menu.id, int(popup)),
    )
    await pages_server.page_system_target_prompt(
        peer, menu,
        title=_srv_title(popup=popup),
        back_data=b"adm:srvnotif",
        everyone_data=f"adm:srvnotif:all:{int(popup)}".encode(),
    )
    return BotCallbackAnswer(message="Укажите получателя или нажмите «Всем».", cache_time=0)


async def begin_srvnotif_broadcast(
        peer: Peer, menu: MessageRef, *, popup: bool, admin_user_id: int,
) -> BotCallbackAnswer:
    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_SYSTEM_TEXT,
        _encode_state("srv", menu.id, int(popup), "all"),
    )
    await pages_server.page_system_text_prompt(
        peer, menu,
        title=_srv_title(popup=popup),
        target_label="Все пользователи",
        back_data=b"adm:srvnotif",
    )
    return BotCallbackAnswer(message="Отправьте текст уведомления.", cache_time=0)


async def begin_fun_target(
        peer: Peer, menu: MessageRef, *, kind: str, admin_user_id: int,
) -> BotCallbackAnswer:
    await AdminBotUserState.set_state(
        admin_user_id, AdminBotState.WAIT_SYSTEM_TARGET, _encode_state("fun", menu.id, kind),
    )
    await pages_server.page_system_target_prompt(
        peer, menu, title="🔐 Фейковый код входа", back_data=b"adm:fun",
    )
    return BotCallbackAnswer(message="Укажите получателя.", cache_time=0)


async def _send_fake_login_code(target_id: int) -> bool:
    from piltover.app.handlers.auth import LOGIN_MESSAGE_FMT

    code = random.randint(10000, 99999)
    text, entities = LOGIN_MESSAGE_FMT.format(code=str(code))
    return await send_official_notification_message(target_id, text, entities)


async def fun_test_popup_action(peer: Peer, menu: MessageRef) -> BotCallbackAnswer:
    await send_service_notification(
        peer.owner_id,
        f"📢 Тестовое service notification от {APP_CONFIG.name}.",
        None,
        popup=True,
    )
    await pages_server.page_server_fun(peer, menu)
    return BotCallbackAnswer(message="Popup отправлен вам.", cache_time=0)


async def handle_system_target_input(
        peer: Peer, message: MessageRef, state: AdminBotUserState,
) -> MessageRef | None:
    from piltover.app.bot_handlers.adminbot.utils import send_bot_message

    text = message.content.message
    if text is None:
        return await send_bot_message(peer, "Отправьте id или @username.")

    mode, extra, menu_id = _parse_state(state.data)
    menu = await _resolve_menu(menu_id)

    if mode == "notify" and is_broadcast_target(text):
        await AdminBotUserState.set_state(
            peer.owner_id,
            AdminBotState.WAIT_SYSTEM_TEXT,
            _encode_state("notify", menu_id or 0, "all"),
        )
        if menu is not None:
            await pages_server.page_system_text_prompt(
                peer, menu,
                title=_notify_title(),
                target_label="Все пользователи",
                back_data=b"adm:notify",
            )
        return await send_bot_message(peer, "Получатель: все. Отправьте текст сообщения.")

    if mode == "srv" and extra and is_broadcast_target(text):
        popup = extra[0] == "1"
        await AdminBotUserState.set_state(
            peer.owner_id,
            AdminBotState.WAIT_SYSTEM_TEXT,
            _encode_state("srv", menu_id or 0, int(popup), "all"),
        )
        if menu is not None:
            await pages_server.page_system_text_prompt(
                peer, menu,
                title=_srv_title(popup=popup),
                target_label="Все пользователи",
                back_data=b"adm:srvnotif",
            )
        return await send_bot_message(peer, "Получатель: все. Отправьте текст уведомления.")

    target = await resolve_notification_target(text)
    if target is None:
        return await send_bot_message(peer, "Пользователь не найден.")

    target_label = target.first_name
    username = await target.get_raw_username()
    if username:
        target_label = f"{target_label} (@{username})"

    if mode == "notify":
        await AdminBotUserState.set_state(
            peer.owner_id,
            AdminBotState.WAIT_SYSTEM_TEXT,
            _encode_state("notify", menu_id or 0, target.id),
        )
        if menu is not None:
            await pages_server.page_system_text_prompt(
                peer, menu,
                title=_notify_title(),
                target_label=target_label,
                back_data=b"adm:notify",
            )
        return await send_bot_message(peer, f"Получатель: {target_label}. Отправьте текст сообщения.")

    if mode == "srv" and extra:
        popup = extra[0] == "1"
        await AdminBotUserState.set_state(
            peer.owner_id,
            AdminBotState.WAIT_SYSTEM_TEXT,
            _encode_state("srv", menu_id or 0, int(popup), target.id),
        )
        if menu is not None:
            await pages_server.page_system_text_prompt(
                peer, menu,
                title=_srv_title(popup=popup),
                target_label=target_label,
                back_data=b"adm:srvnotif",
            )
        return await send_bot_message(peer, f"Получатель: {target_label}. Отправьте текст уведомления.")

    if mode == "fun" and extra and extra[0] == "code":
        ok = await _send_fake_login_code(target.id)
        await state.delete()
        if menu is not None:
            await pages_server.page_server_fun(peer, menu)
        if not ok:
            return await send_bot_message(peer, "Системный пользователь 777000 не найден.")
        return await send_bot_message(peer, f"🔐 Фейковый код входа отправлен: {target_label}.")

    return await send_bot_message(peer, "Неизвестное действие.")


async def handle_system_text_input(
        peer: Peer, message: MessageRef, state: AdminBotUserState,
) -> MessageRef | None:
    from piltover.app.bot_handlers.adminbot.utils import send_bot_message

    body = message.content.message
    if body is None or not body.strip():
        return await send_bot_message(peer, "Сообщение не может быть пустым.")

    mode, extra, menu_id = _parse_state(state.data)
    menu = await _resolve_menu(menu_id)
    if not extra:
        return await send_bot_message(peer, "Сессия истекла.")

    broadcast = extra[-1] == "all"
    if mode == "notify":
        if not broadcast:
            try:
                target_id = int(extra[-1])
            except ValueError:
                return await send_bot_message(peer, "Сессия истекла.")
    elif mode == "srv":
        popup = extra[0] == "1"
        if not broadcast:
            try:
                target_id = int(extra[-1])
            except ValueError:
                return await send_bot_message(peer, "Сессия истекла.")
    else:
        return await send_bot_message(peer, "Неизвестное действие.")

    await state.delete()
    if menu is not None:
        await pages_server.page_server_menu(peer, menu)

    if mode == "notify":
        if broadcast:
            sent = await broadcast_official_notification_message(body, None)
            if sent == 0:
                return await send_bot_message(peer, "Системный пользователь 777000 не найден.")
            return await send_bot_message(peer, f"✅ Сообщение отправлено {sent} пользователям.")
        target = await User.get_or_none(id=target_id, deleted=False)
        if target is None:
            return await send_bot_message(peer, "Пользователь не найден.")
        target_label = target.first_name
        username = await target.get_raw_username()
        if username:
            target_label = f"{target_label} (@{username})"
        ok = await send_official_notification_message(target_id, body, None)
        if not ok:
            return await send_bot_message(peer, "Системный пользователь 777000 не найден.")
        return await send_bot_message(peer, f"✅ Сообщение отправлено: {target_label}.")

    style = "Popup" if popup else "Тихое"
    if broadcast:
        sent = await broadcast_service_notification(body, None, popup=popup)
        return await send_bot_message(
            peer, f"✅ {style} уведомление отправлено {sent} пользователям.",
        )

    target = await User.get_or_none(id=target_id, deleted=False)
    if target is None:
        return await send_bot_message(peer, "Пользователь не найден.")
    target_label = target.first_name
    username = await target.get_raw_username()
    if username:
        target_label = f"{target_label} (@{username})"
    await send_service_notification(target_id, body, None, popup=popup)
    return await send_bot_message(peer, f"✅ {style} уведомление отправлено: {target_label}.")