from __future__ import annotations

from piltover.app.bot_handlers.adminbot.callback_data import (
    back_list_data,
    encode_list_key,
    encode_user_list_key,
    stars_action,
    user_action,
    user_link,
    users_list_callback,
)
from piltover.app.bot_handlers.adminbot.utils import (
    HOME,
    PAGE_SIZE,
    back_home_row,
    hide_row,
    home_keyboard,
    list_keyboard,
    push_bot_message,
    user_label,
)
from piltover.app.bot_handlers.typetestbot.common import edit_bot_message
from piltover.app.utils.admin_memberships import get_user_memberships
from piltover.app.utils.admin_sessions import get_user_sessions
from piltover.db.models import Channel, Chat, MessageRef, Peer, User, UserStarsBalance, Username
from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, ReplyInlineMarkup


async def page_home(peer: Peer, menu: MessageRef) -> MessageRef:
    text = (
        "🛡 Админ-панель\n\n"
        "Администрирование сервера. Выберите категорию ниже."
    )
    return await edit_bot_message(menu, peer, text, home_keyboard())


async def _usernames_for_users(users: list[User]) -> dict[int, str | None]:
    if not users:
        return {}
    user_ids = [user.id for user in users]
    return {
        user_id: username
        for user_id, username in await Username.filter(user_id__in=user_ids).values_list("user_id", "username")
    }


async def _stars_amount(user_id: int) -> int:
    balance = await UserStarsBalance.get_or_create_for(user_id)
    return balance.amount


def _user_nav_row(list_key: str) -> KeyboardButtonRow:
    return KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Пользователи", data=back_list_data(list_key)),
        KeyboardButtonCallback(text="« Главное меню", data=HOME),
    ])


async def _fetch_users(*, show_system: bool) -> list[User]:
    query = User.filter(bot=False, deleted=False)
    if not show_system:
        query = query.filter(system=False)
    return list(await query.order_by("-system", "-id"))


async def page_users(peer: Peer, page: int, menu: MessageRef, *, show_system: bool = False) -> MessageRef:
    users = await _fetch_users(show_system=show_system)
    total = len(users)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE) if total else 1
    page = max(0, min(page, total_pages - 1))
    list_key = encode_user_list_key(page, show_system=show_system)
    chunk = users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE] if total else []
    usernames = await _usernames_for_users(chunk)

    scope = "все пользователи" if show_system else "обычные пользователи"
    items = [
        (user_label(user, username=usernames.get(user.id)), user_link(user.id, list_key))
        for user in chunk
    ]
    text = f"👥 Пользователи ({total}, {scope}). Нажмите для управления:"
    page_prefix = b"adm:users:sys" if show_system else b"adm:users"
    keyboard = list_keyboard(
        items=items, page=page, total_pages=total_pages, page_prefix=page_prefix,
    )
    toggle_label = "✅ Показать системные" if show_system else "☑️ Показать системные"
    keyboard.rows.insert(0, KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="🔍 Найти", data=b"adm:find:user"),
        KeyboardButtonCallback(text=toggle_label, data=users_list_callback(page, show_system=not show_system)),
    ]))
    keyboard.rows.insert(0, KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="🗑 Удалённые аккаунты", data=b"adm:del:0"),
    ]))
    if total == 0:
        text = f"👥 Нет пользователей ({scope})."
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_admins(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    users = list(await User.filter(admin=True, bot=False, deleted=False).order_by("-id"))
    total = len(users)
    if total == 0:
        return await edit_bot_message(menu, peer, "Администраторы не найдены.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:admins", back_data=HOME,
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    list_key = encode_list_key("a", page)
    chunk = users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    usernames = await _usernames_for_users(chunk)

    items = [
        (user_label(user, username=usernames.get(user.id)), user_link(user.id, list_key))
        for user in chunk
    ]
    text = f"Администраторы ({total}). Нажмите для управления:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:admins")
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_channels(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    channels = list(await Channel.filter(deleted=False).order_by("-id"))
    total = len(channels)
    if total == 0:
        return await edit_bot_message(menu, peer, "Нет каналов.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:channels",
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    list_key = encode_list_key("c", page)
    chunk = channels[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    items: list[tuple[str, bytes]] = []
    for channel in chunk:
        kind = "канал" if channel.channel else "супергруппа"
        badge = " ✓" if channel.verified else ""
        label = f"[{kind}] {channel.name}{badge}"
        items.append((label[:64], f"adm:ch:{channel.id}:{list_key}".encode()))

    text = f"Каналы и супергруппы ({total}). Нажмите для профиля:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:channels")
    keyboard.rows.insert(0, KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="🔍 Найти канал", data=b"adm:find:ch"),
    ]))
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_groups(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    chats = list(await Chat.filter(deleted=False, migrated=False).order_by("-id"))
    total = len(chats)
    if total == 0:
        return await edit_bot_message(menu, peer, "Нет обычных групп.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:groups",
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    list_key = encode_list_key("g", page)
    chunk = chats[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    items: list[tuple[str, bytes]] = []
    for chat in chunk:
        badge = " ✓" if chat.verified else ""
        label = f"[группа] {chat.name}{badge}"
        items.append((label[:64], f"adm:gr:{chat.id}:{list_key}".encode()))

    text = f"Обычные группы ({total}). Нажмите для профиля:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:groups")
    keyboard.rows.insert(0, KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="🔍 Найти группу", data=b"adm:find:gr"),
    ]))
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_stats(peer: Peer, menu: MessageRef) -> MessageRef:
    users = await User.filter(bot=False, system=False, deleted=False).count()
    bots = await User.filter(bot=True, system=False, deleted=False).count()
    admins = await User.filter(admin=True, bot=False, deleted=False).count()
    channels = await Channel.filter(deleted=False, channel=True).count()
    supergroups = await Channel.filter(deleted=False, supergroup=True).count()
    groups = await Chat.filter(deleted=False, migrated=False).count()
    verified_users = await User.filter(verified=True, deleted=False).count()
    spam_blocked = await User.filter(spam_blocked=True, bot=False, deleted=False).count()
    deleted_accounts = await User.filter(deleted=True, system=False).count()
    from piltover.db.models import AdminReport
    pending_reports = await AdminReport.filter(reviewed=False).count()

    text = (
        "📊 Статистика сервера\n\n"
        f"Пользователи: {users}\n"
        f"Боты: {bots}\n"
        f"Администраторы: {admins}\n"
        f"Удалённые аккаунты: {deleted_accounts}\n"
        f"Верифицированные: {verified_users}\n"
        f"Заблокированы за спам: {spam_blocked}\n"
        f"Ожидающие жалобы: {pending_reports}\n"
        f"Каналы: {channels}\n"
        f"Супергруппы: {supergroups}\n"
        f"Обычные группы: {groups}"
    )
    keyboard = ReplyInlineMarkup(rows=[back_home_row()])
    return await edit_bot_message(menu, peer, text, keyboard)


async def _get_user_profile(user_id: int) -> User | None:
    return await User.get_or_none(id=user_id, bot=False, deleted=False)


async def page_user(
        peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0", overlay: bool = False,
) -> MessageRef:
    user = await _get_user_profile(user_id)
    if user is None:
        markup = ReplyInlineMarkup(rows=[back_home_row()])
        if overlay:
            return await push_bot_message(peer, "Пользователь не найден.", markup)
        return await edit_bot_message(menu, peer, "Пользователь не найден.", markup)

    username = await user.get_raw_username()
    stars = await _stars_amount(user.id)
    sessions = await get_user_sessions(user.id)
    memberships = await get_user_memberships(user.id)

    lines = [
        f"👤 {user.first_name}" + (f" {user.last_name}" if user.last_name else ""),
        f"ID: {user.id}",
    ]
    if user.system:
        lines.append("Тип: системный аккаунт ⚙")
    if username:
        lines.append(f"Имя пользователя: @{username}")
    if user.phone_number:
        lines.append(f"Телефон: {user.phone_number}")
    lines.append(f"Админ: {'да' if user.admin else 'нет'}")
    lines.append(f"Верифицирован: {'да ✓' if user.verified else 'нет'}")
    lines.append(f"Поддержка: {'да' if user.support else 'нет'}")
    if not user.system:
        lines.append(f"Заблокирован за спам: {'да' if user.spam_blocked else 'нет'}")
        lines.append(f"Звёзды: ⭐ {stars}")
    lines.append(f"Сессии: {len(sessions)}")
    if not user.system:
        lines.append(f"Админские членства: {len(memberships)}")

    rows: list[KeyboardButtonRow] = []
    if not user.system:
        if user.admin:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="Отозвать админа", data=user_action("unadmin", user.id, list_key)),
            ]))
        else:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="Выдать админа", data=user_action("admin", user.id, list_key)),
            ]))

    if user.verified:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Снять галочку", data=user_action("unverify", user.id, list_key)),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Выдать галочку", data=user_action("verify", user.id, list_key)),
        ]))

    if user.support:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Снять поддержку", data=user_action("unsupport", user.id, list_key)),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Выдать поддержку", data=user_action("support", user.id, list_key)),
        ]))

    if not user.system:
        if user.spam_blocked:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="Снять блокировку за спам", data=user_action("unspam", user.id, list_key)),
            ]))
        else:
            rows.append(KeyboardButtonRow(buttons=[
                KeyboardButtonCallback(text="Заблокировать за спам", data=user_action("spam", user.id, list_key)),
            ]))
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="⚙️ Профиль", data=f"adm:user:set:{user.id}:{list_key}".encode()),
            KeyboardButtonCallback(text="⭐ Звёзды", data=f"adm:user:stars:{user.id}:{list_key}".encode()),
        ]))
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📱 Сессии", data=f"adm:user:sess:{user.id}:{list_key}".encode()),
        ]))
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📋 Членства", data=f"adm:user:mem:{user.id}:0:{list_key}".encode()),
        ]))
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="🗑 Удалить аккаунт", data=user_action("deluser", user.id, list_key)),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="📱 Сессии", data=f"adm:user:sess:{user.id}:{list_key}".encode()),
        ]))
    if overlay:
        rows.append(hide_row())
    else:
        rows.append(_user_nav_row(list_key))

    markup = ReplyInlineMarkup(rows=rows)
    if overlay:
        return await push_bot_message(peer, "\n".join(lines), markup)
    return await edit_bot_message(menu, peer, "\n".join(lines), markup)


async def page_user_stars(peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0") -> MessageRef:
    user = await _get_user_profile(user_id)
    if user is None:
        return await edit_bot_message(menu, peer, "Пользователь не найден.", ReplyInlineMarkup(rows=[back_home_row()]))

    stars = await _stars_amount(user.id)
    text = f"⭐ Звёзды для {user.first_name}\n\nТекущий баланс: {stars}"

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="+25", data=stars_action("add", user.id, 25, list_key)),
            KeyboardButtonCallback(text="+100", data=stars_action("add", user.id, 100, list_key)),
            KeyboardButtonCallback(text="+500", data=stars_action("add", user.id, 500, list_key)),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Установить 0", data=stars_action("set", user.id, 0, list_key)),
            KeyboardButtonCallback(text="Установить 100", data=stars_action("set", user.id, 100, list_key)),
            KeyboardButtonCallback(text="Установить 1000", data=stars_action("set", user.id, 1000, list_key)),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Другая сумма", data=stars_action("custom", user.id, 0, list_key)),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« К пользователю", data=user_link(user.id, list_key)),
        ]),
    ]
    return await edit_bot_message(menu, peer, text, ReplyInlineMarkup(rows=rows))


async def page_user_sessions(peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0") -> MessageRef:
    user = await _get_user_profile(user_id)
    if user is None:
        return await edit_bot_message(menu, peer, "Пользователь не найден.", ReplyInlineMarkup(rows=[back_home_row()]))

    sessions = await get_user_sessions(user.id)
    lines = [f"📱 Сессии для {user.first_name} ({len(sessions)})", ""]
    if not sessions:
        lines.append("Нет активных сессий.")
    else:
        for idx, session in enumerate(sessions[:10], start=1):
            lines.append(
                f"{idx}. {session.platform} / {session.device_model}\n"
                f"   {session.system_version} · {session.app_version}\n"
                f"   IP {session.ip}"
            )
        if len(sessions) > 10:
            lines.append(f"... и ещё {len(sessions) - 10}")

    rows: list[KeyboardButtonRow] = []
    if sessions:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Отключить все сессии", data=user_action("kick", user.id, list_key)),
        ]))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« К пользователю", data=user_link(user.id, list_key)),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_user_memberships(
        peer: Peer, user_id: int, page: int, menu: MessageRef, *, list_key: str = "u0",
) -> MessageRef:
    user = await _get_user_profile(user_id)
    if user is None:
        return await edit_bot_message(menu, peer, "Пользователь не найден.", ReplyInlineMarkup(rows=[back_home_row()]))

    memberships = await get_user_memberships(user.id)
    total = len(memberships)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = memberships[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [f"📋 Членства для {user.first_name} ({total})", ""]
    if not chunk:
        lines.append("Нет админских групп, каналов или принадлежащих ботов.")
    else:
        for item in chunk:
            lines.append(f"[{item.kind}/{item.role}] {item.title} (id {item.entity_id})")

    rows: list[KeyboardButtonRow] = []
    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(
            text="« Назад", data=f"adm:user:mem:{user.id}:{page - 1}:{list_key}".encode(),
        ))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(
            text="Вперёд »", data=f"adm:user:mem:{user.id}:{page + 1}:{list_key}".encode(),
        ))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))

    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« К пользователю", data=user_link(user.id, list_key)),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))