from __future__ import annotations

from piltover.app.bot_handlers.adminbot.utils import back_home_row
from piltover.app.bot_handlers.typetestbot.common import edit_bot_message
from piltover.app.utils.server_settings import get_server_settings
from piltover.config import APP_CONFIG
from piltover.db.models import MessageRef, Peer
from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, ReplyInlineMarkup


def _toggle_label(name: str, enabled: bool) -> str:
    return f"{'✅' if enabled else '❌'} {name}"


async def page_server_menu(peer: Peer, menu: MessageRef) -> MessageRef:
    text = (
        "📣 Инструменты сервера\n\n"
        f"Системный аккаунт: +42777 (@{APP_CONFIG.system_user_username})\n"
        "Рассылка уведомлений и настройки сервера."
    )
    keyboard = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📨 От +42777", data=b"adm:notify"),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🔔 Service notification", data=b"adm:srvnotif"),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🎭 Приколы", data=b"adm:fun"),
            KeyboardButtonCallback(text="⚙️ Конфиг", data=b"adm:cfg"),
        ]),
        KeyboardButtonRow(buttons=[back_home_row().buttons[0]]),
    ])
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_server_config(peer: Peer, menu: MessageRef) -> MessageRef:
    settings = await get_server_settings()
    text = (
        "⚙️ Конфиг сервера\n\n"
        "Нажмите на строку, чтобы переключить настройку."
    )
    keyboard = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Репорты", settings.reports_enabled),
                data=b"adm:cfg:reports",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Создание ботов", settings.bot_creation_enabled),
                data=b"adm:cfg:bots",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Создание групп", settings.group_creation_enabled),
                data=b"adm:cfg:groups",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Создание каналов", settings.channel_creation_enabled),
                data=b"adm:cfg:channels",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Звонки", settings.phone_calls_enabled),
                data=b"adm:cfg:calls",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Verify Bot", settings.verifybot_enabled),
                data=b"adm:cfg:verifybot",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(
                text=_toggle_label("Stars Bot", settings.stars_bot_enabled),
                data=b"adm:cfg:stars",
            ),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Назад", data=b"adm:server"),
        ]),
        KeyboardButtonRow(buttons=[back_home_row().buttons[0]]),
    ])
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_server_fun(peer: Peer, menu: MessageRef) -> MessageRef:
    text = (
        "🎭 Приколы\n\n"
        "Безобидные штуки для тестов."
    )
    keyboard = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🔐 Фейковый код входа", data=b"adm:fun:code"),
            KeyboardButtonCallback(text="🔔 Тест popup (мне)", data=b"adm:fun:popup"),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Назад", data=b"adm:server"),
        ]),
        KeyboardButtonRow(buttons=[back_home_row().buttons[0]]),
    ])
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_system_target_prompt(
        peer: Peer, menu: MessageRef, *, title: str, back_data: bytes = b"adm:server",
        everyone_data: bytes | None = None,
) -> MessageRef:
    if everyone_data is not None:
        text = (
            f"{title}\n\n"
            "Отправьте id, @username или номер телефона.\n"
            "Или нажмите «Всем» для рассылки."
        )
    else:
        text = f"{title}\n\nОтправьте id, @username или номер телефона."
    rows: list[KeyboardButtonRow] = []
    if everyone_data is not None:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🌍 Всем", data=everyone_data),
        ]))
    rows.extend([
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Назад", data=back_data),
        ]),
        KeyboardButtonRow(buttons=[back_home_row().buttons[0]]),
    ])
    return await edit_bot_message(menu, peer, text, ReplyInlineMarkup(rows=rows))


async def page_system_text_prompt(
        peer: Peer, menu: MessageRef, *, title: str, target_label: str, back_data: bytes = b"adm:server",
) -> MessageRef:
    text = f"{title}\n\nПолучатель: {target_label}\nОтправьте текст сообщения."
    keyboard = ReplyInlineMarkup(rows=[
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Отмена", data=back_data),
        ]),
        KeyboardButtonRow(buttons=[back_home_row().buttons[0]]),
    ])
    return await edit_bot_message(menu, peer, text, keyboard)