from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.bot_handlers.adminbot import pages, pages_extended
from piltover.app.bot_handlers.adminbot.callback_data import (
    encode_stars_wait_data, parse_bot_list_key, parse_user_list_key,
)
from piltover.app.utils import verification
from piltover.app.utils.admin_sessions import kick_all_user_sessions
from piltover.app.utils.admin_users import LastAdminError, set_user_admin
from piltover.app.utils.spam_block import set_user_spam_blocked
from piltover.app.utils.stars_manager import grant_stars, set_stars_balance
from piltover.db.enums import AdminBotState
from piltover.app.utils.admin_channel_ops import (
    admin_delete_bot, delete_channel_admin, kick_channel_member, kick_chat_member,
    promote_channel_admin, promote_chat_admin, transfer_channel_owner, transfer_chat_owner,
)
from piltover.app.utils.admin_delete_user import (
    RestoreAccountError, admin_delete_user, admin_restore_bot, admin_restore_user,
)
from piltover.app.bot_handlers.adminbot.utils import hide_bot_message
from piltover.db.models import AdminBotUserState, AdminReport, Bot, Channel, Chat, MessageRef, Peer, User
from piltover.tl.types.messages import BotCallbackAnswer


async def toggle_user_admin(
        peer: Peer, menu: MessageRef, user_id: int, admin: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    try:
        changed = await set_user_admin(user, admin)
    except LastAdminError as exc:
        return BotCallbackAnswer(message=str(exc), alert=True, cache_time=0)

    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    action = "выданы" if admin else "отозваны"
    return BotCallbackAnswer(message=f"Права администратора {action}.", cache_time=0)


async def toggle_user_support(
        peer: Peer, menu: MessageRef, user_id: int, support: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, deleted=False)
    if user is None or user.bot:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    changed = await verification.set_user_support(user, support)
    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    action = "выдан" if support else "снят"
    return BotCallbackAnswer(message=f"Флаг поддержки {action}.", cache_time=0)


async def toggle_user_verified(
        peer: Peer, menu: MessageRef, user_id: int, verified: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, deleted=False)
    if user is None or user.bot:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    changed = await verification.set_user_verified(user, verified)
    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    action = "выдана" if verified else "снята"
    return BotCallbackAnswer(message=f"Галочка {action}.", cache_time=0)


async def toggle_user_spam_block(
        peer: Peer, menu: MessageRef, user_id: int, blocked: bool, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    changed = await set_user_spam_blocked(user, blocked)
    await pages.page_user(peer, user_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    action = "применён" if blocked else "снят"
    return BotCallbackAnswer(message=f"Спам-блок {action}.", cache_time=0)


async def kick_user_sessions_action(
        peer: Peer, menu: MessageRef, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    count = await kick_all_user_sessions(user.id)
    await pages.page_user_sessions(peer, user_id, menu, list_key=list_key)
    if count == 0:
        return BotCallbackAnswer(message="Нет сессий для отключения.", cache_time=0)
    return BotCallbackAnswer(message=f"Отключено сессий: {count}.", cache_time=0)


async def toggle_channel_verified(
        peer: Peer, menu: MessageRef, channel_id: int, verified: bool, *, list_key: str = "c0",
) -> BotCallbackAnswer:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return BotCallbackAnswer(message="Канал не найден.", alert=True, cache_time=0)

    changed = await verification.set_channel_verified(channel, verified)
    await pages_extended.page_channel(peer, channel_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    action = "выдана" if verified else "снята"
    return BotCallbackAnswer(message=f"Галочка {action}.", cache_time=0)


async def toggle_chat_verified(
        peer: Peer, menu: MessageRef, chat_id: int, verified: bool, *, list_key: str = "g0",
) -> BotCallbackAnswer:
    chat = await Chat.get_or_none(id=chat_id, deleted=False, migrated=False)
    if chat is None:
        return BotCallbackAnswer(message="Группа не найдена.", alert=True, cache_time=0)

    changed = await verification.set_chat_verified(chat, verified)
    await pages_extended.page_group(peer, chat_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    action = "выдана" if verified else "снята"
    return BotCallbackAnswer(message=f"Галочка {action}.", cache_time=0)


async def grant_user_stars(
        peer: Peer, menu: MessageRef, user_id: int, amount: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    balance = await grant_stars(
        user_id,
        amount,
        title="Начисление администратором",
        description=f"Начислено {amount} звёзд через @admin",
    )
    await upd.update_stars_balance(user_id, balance.to_stars_amount())
    await pages.page_user_stars(peer, user_id, menu, list_key=list_key)
    return BotCallbackAnswer(message=f"Начислено {amount} звёзд.", cache_time=0)


async def set_user_stars(
        peer: Peer, menu: MessageRef, user_id: int, amount: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    balance = await set_stars_balance(
        user_id,
        amount,
        title="Установка администратором",
        description=f"Баланс установлен на {amount} звёзд через @admin",
    )
    await upd.update_stars_balance(user_id, balance.to_stars_amount())
    await pages.page_user_stars(peer, user_id, menu, list_key=list_key)
    return BotCallbackAnswer(message=f"Баланс установлен на {amount} звёзд.", cache_time=0)


async def delete_user_action(
        peer: Peer, menu: MessageRef, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)
    if user.admin and await User.filter(admin=True, bot=False, deleted=False).count() <= 1:
        return BotCallbackAnswer(message="Нельзя удалить последнего администратора.", alert=True, cache_time=0)

    if user.system:
        return BotCallbackAnswer(message="Нельзя удалить служебный аккаунт.", alert=True, cache_time=0)

    kicked = await admin_delete_user(user)
    page, show_system = parse_user_list_key(list_key)
    await pages.page_users(peer, page, menu, show_system=show_system)
    return BotCallbackAnswer(message=f"Пользователь удалён. Отключено сессий: {kicked}.", cache_time=0)


async def delete_bot_action(peer: Peer, menu: MessageRef, bot_id: int, *, list_key: str) -> BotCallbackAnswer:
    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)
    if bot_user.system:
        return BotCallbackAnswer(message="Нельзя удалить системного бота.", alert=True, cache_time=0)
    await admin_delete_bot(bot_user)
    page, show_system = parse_bot_list_key(list_key)
    await pages_extended.page_bots(peer, page, menu, show_system=show_system)
    return BotCallbackAnswer(message="Бот удалён.", cache_time=0)


async def restore_deleted_account_action(
        peer: Peer, menu: MessageRef, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, deleted=True, system=False)
    if user is None:
        return BotCallbackAnswer(message="Аккаунт не найден.", alert=True, cache_time=0)

    try:
        if user.bot:
            await admin_restore_bot(user)
            label = "Бот восстановлен."
        else:
            await admin_restore_user(user)
            label = "Пользователь восстановлен."
    except RestoreAccountError as exc:
        return BotCallbackAnswer(message=str(exc), alert=True, cache_time=0)

    page = int(list_key[1:]) if list_key.startswith("d") and list_key[1:].isdigit() else 0
    await pages_extended.page_deleted_users(peer, page, menu)
    return BotCallbackAnswer(message=label, cache_time=0)


async def delete_channel_action(
        peer: Peer, menu: MessageRef, channel_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return BotCallbackAnswer(message="Канал не найден.", alert=True, cache_time=0)
    await delete_channel_admin(channel)
    await pages.page_channels(peer, 0, menu)
    return BotCallbackAnswer(message="Канал удалён.", cache_time=0)


async def kick_channel_member_action(
        peer: Peer, menu: MessageRef, channel_id: int, target_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return BotCallbackAnswer(message="Не найдено.", alert=True, cache_time=0)
    await kick_channel_member(channel, target_id)
    await pages_extended.page_channel_members(peer, channel_id, 0, menu, list_key=list_key)
    return BotCallbackAnswer(message="Участник исключён.", cache_time=0)


async def kick_group_member_action(
        peer: Peer, menu: MessageRef, chat_id: int, target_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    chat = await Chat.get_or_none(id=chat_id, deleted=False)
    if chat is None:
        return BotCallbackAnswer(message="Не найдено.", alert=True, cache_time=0)
    await kick_chat_member(chat, target_id, actor_id=peer.owner_id)
    await pages_extended.page_group_members(peer, chat_id, 0, menu, list_key=list_key)
    return BotCallbackAnswer(message="Участник исключён.", cache_time=0)


async def promote_channel_admin_action(
        peer: Peer, menu: MessageRef, channel_id: int, target_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    channel = await Channel.get_or_none(id=channel_id, deleted=False)
    if channel is None:
        return BotCallbackAnswer(message="Не найдено.", alert=True, cache_time=0)
    try:
        await promote_channel_admin(channel, target_id)
    except ValueError:
        return BotCallbackAnswer(message="Не является участником.", alert=True, cache_time=0)
    await pages_extended.page_channel_members(peer, channel_id, 0, menu, list_key=list_key)
    return BotCallbackAnswer(message="Права администратора выданы.", cache_time=0)


async def promote_group_admin_action(
        peer: Peer, menu: MessageRef, chat_id: int, target_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    chat = await Chat.get_or_none(id=chat_id, deleted=False)
    if chat is None:
        return BotCallbackAnswer(message="Не найдено.", alert=True, cache_time=0)
    try:
        await promote_chat_admin(chat, target_id)
    except ValueError:
        return BotCallbackAnswer(message="Не является участником.", alert=True, cache_time=0)
    await pages_extended.page_group_members(peer, chat_id, 0, menu, list_key=list_key)
    return BotCallbackAnswer(message="Права администратора выданы.", cache_time=0)


async def toggle_bot_verified(
        peer: Peer, menu: MessageRef, bot_id: int, verified: bool, *, list_key: str,
) -> BotCallbackAnswer:
    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)
    changed = await verification.set_user_verified(bot_user, verified)
    await pages_extended.page_bot(peer, bot_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    return BotCallbackAnswer(message="Галочка обновлена.", cache_time=0)


async def toggle_bot_system(
        peer: Peer, menu: MessageRef, bot_id: int, system: bool, *, list_key: str, confirm: bool = False,
) -> BotCallbackAnswer:
    from piltover.app.utils.admin_access import is_builtin_admin_bot

    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)
    if not system and await is_builtin_admin_bot(bot_user) and not confirm:
        await pages_extended.page_bot_unsystem_warning(peer, bot_id, menu, list_key=list_key)
        return BotCallbackAnswer(
            message="⚠️ Снятие системного статуса с @admin отключит встроенные обработчики.",
            alert=True,
            cache_time=0,
        )
    if bot_user.system == system:
        return BotCallbackAnswer(message="Уже актуально.", cache_time=0)
    bot_user.system = system
    await bot_user.save(update_fields=["system", "version"])
    await bot_user.inc_version()
    await upd.update_user(bot_user)
    await pages_extended.page_bot(peer, bot_id, menu, list_key=list_key)
    action = "помечен как системный" if system else "снята пометка системного"
    return BotCallbackAnswer(message=f"Бот {action}.", cache_time=0)


async def revoke_bot_token_action(
        peer: Peer, menu: MessageRef, bot_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    from piltover.db.models.bot import bot_gen_token

    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)
    bot_row = await Bot.get_or_none(bot_id=bot_id)
    if bot_row is None:
        return BotCallbackAnswer(message="Запись бота не найдена.", alert=True, cache_time=0)

    bot_row.token_nonce = bot_gen_token()
    await bot_row.save(update_fields=["token_nonce"])
    await pages_extended.page_bot_token(peer, bot_id, menu, list_key=list_key)
    return BotCallbackAnswer(message="Токен отозван.", cache_time=0)


async def hide_message_action(peer: Peer, menu: MessageRef) -> BotCallbackAnswer:
    await hide_bot_message(peer, menu)
    return BotCallbackAnswer(message="Скрыто.", cache_time=0)


async def spam_from_report_action(
        peer: Peer, menu: MessageRef, report_id: int, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    changed = await set_user_spam_blocked(user, True)
    await pages_extended.page_report(peer, report_id, menu, list_key=list_key)
    if not changed:
        return BotCallbackAnswer(message="Уже заблокирован за спам.", cache_time=0)
    return BotCallbackAnswer(message="Спам-блок применён.", cache_time=0)


async def spam_author_from_report_action(
        peer: Peer, menu: MessageRef, report_id: int, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    return await spam_from_report_action(peer, menu, report_id, user_id, list_key=list_key)


async def ban_user_from_report_action(
        peer: Peer, menu: MessageRef, report_id: int, user_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)
    if user.admin and await User.filter(admin=True, bot=False, deleted=False).count() <= 1:
        return BotCallbackAnswer(message="Нельзя удалить последнего администратора.", alert=True, cache_time=0)

    kicked = await admin_delete_user(user)
    await pages_extended.page_report(peer, report_id, menu, list_key=list_key)
    return BotCallbackAnswer(message=f"Пользователь заблокирован. Отключено сессий: {kicked}.", cache_time=0)


async def ban_bot_from_report_action(
        peer: Peer, menu: MessageRef, report_id: int, bot_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)
    if bot_user.system:
        return BotCallbackAnswer(message="Нельзя удалить системного бота.", alert=True, cache_time=0)

    await admin_delete_bot(bot_user)
    await pages_extended.page_report(peer, report_id, menu, list_key=list_key)
    return BotCallbackAnswer(message="Бот заблокирован.", cache_time=0)


async def review_report_action(
        peer: Peer, menu: MessageRef, report_id: int, *, list_key: str,
) -> BotCallbackAnswer:
    report = await AdminReport.get_or_none(id=report_id)
    if report is None:
        return BotCallbackAnswer(message="Жалоба не найдена.", alert=True, cache_time=0)
    report.reviewed = True
    await report.save(update_fields=["reviewed"])
    await pages_extended.page_report(peer, report_id, menu, list_key=list_key)
    return BotCallbackAnswer(message="Отмечено как рассмотренное.", cache_time=0)


async def begin_search_input(
        peer: Peer, menu: MessageRef, kind: str, *, admin_user_id: int,
) -> BotCallbackAnswer:
    from piltover.app.utils.admin_search import SearchFilters

    filters = SearchFilters(kind=kind)
    await AdminBotUserState.set_state(admin_user_id, AdminBotState.WAIT_SEARCH, filters.encode())
    await pages_extended.page_search_prompt(peer, menu, filters=filters)
    return BotCallbackAnswer(message="Отправьте поисковый запрос в чат.", cache_time=0)


async def toggle_search_filter(
        peer: Peer, menu: MessageRef, flag: str, *, admin_user_id: int,
) -> BotCallbackAnswer:
    from piltover.app.utils.admin_search import SearchFilters

    state = await AdminBotUserState.get_or_none(user_id=admin_user_id)
    if state is None or state.state is not AdminBotState.WAIT_SEARCH:
        return BotCallbackAnswer(message="Сессия поиска истекла.", alert=True, cache_time=0)

    filters = SearchFilters.decode(state.data)
    filters.toggle(flag)
    await AdminBotUserState.set_state(admin_user_id, AdminBotState.WAIT_SEARCH, filters.encode())
    await pages_extended.page_search_prompt(peer, menu, filters=filters)
    return BotCallbackAnswer(cache_time=0)


async def clear_bot_field(
        peer: Peer, menu: MessageRef, bot_id: int, field: str, *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    from piltover.app.utils.admin_bot_edit import CLEARABLE_BOT_FIELDS, apply_bot_field_value
    from piltover.db.enums import AdminBotState

    if field not in CLEARABLE_BOT_FIELDS:
        return BotCallbackAnswer(message="Это поле нельзя очистить.", alert=True, cache_time=0)

    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)

    error = await apply_bot_field_value(bot_user, field, "", clear=True)
    if error is not None:
        return BotCallbackAnswer(message=error, alert=True, cache_time=0)

    await AdminBotUserState.filter(user_id=admin_user_id, state=AdminBotState.WAIT_BOT_EDIT).delete()
    await pages_extended.page_bot_settings(peer, bot_id, menu, list_key=list_key)
    return BotCallbackAnswer(message="Очищено.", cache_time=0)


_ENTITY_CLEARABLE = {
    "user": frozenset({"lastname", "username", "about", "phone"}),
    "ch": frozenset({"about", "username"}),
    "gr": frozenset({"about"}),
}


async def clear_entity_field(
        peer: Peer, menu: MessageRef, kind: str, entity_id: int, field: str,
        *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    from piltover.app.utils.admin_entity_edit import (
        apply_channel_field_value, apply_group_field_value, apply_user_field_value,
    )
    from piltover.db.enums import AdminBotState
    from piltover.db.models import Channel, Chat

    if field not in _ENTITY_CLEARABLE.get(kind, frozenset()):
        return BotCallbackAnswer(message="Это поле нельзя очистить.", alert=True, cache_time=0)

    if kind == "user":
        user = await User.get_or_none(id=entity_id, bot=False, deleted=False)
        if user is None:
            return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)
        error = await apply_user_field_value(user, field, "", clear=True)
        if error is not None:
            return BotCallbackAnswer(message=error, alert=True, cache_time=0)
        await AdminBotUserState.filter(user_id=admin_user_id, state=AdminBotState.WAIT_ENTITY_EDIT).delete()
        await pages_extended.page_user_settings(peer, entity_id, menu, list_key=list_key)
        return BotCallbackAnswer(message="Очищено.", cache_time=0)

    if kind == "ch":
        channel = await Channel.get_or_none(id=entity_id, deleted=False)
        if channel is None:
            return BotCallbackAnswer(message="Канал не найден.", alert=True, cache_time=0)
        error = await apply_channel_field_value(channel, field, "", clear=True)
        if error is not None:
            return BotCallbackAnswer(message=error, alert=True, cache_time=0)
        await AdminBotUserState.filter(user_id=admin_user_id, state=AdminBotState.WAIT_ENTITY_EDIT).delete()
        await pages_extended.page_channel_settings(peer, entity_id, menu, list_key=list_key)
        return BotCallbackAnswer(message="Очищено.", cache_time=0)

    if kind == "gr":
        chat = await Chat.get_or_none(id=entity_id, deleted=False, migrated=False)
        if chat is None:
            return BotCallbackAnswer(message="Группа не найдена.", alert=True, cache_time=0)
        error = await apply_group_field_value(chat, field, "", clear=True)
        if error is not None:
            return BotCallbackAnswer(message=error, alert=True, cache_time=0)
        await AdminBotUserState.filter(user_id=admin_user_id, state=AdminBotState.WAIT_ENTITY_EDIT).delete()
        await pages_extended.page_group_settings(peer, entity_id, menu, list_key=list_key)
        return BotCallbackAnswer(message="Очищено.", cache_time=0)

    return BotCallbackAnswer(message="Неизвестная сущность.", alert=True, cache_time=0)


async def begin_entity_edit_input(
        peer: Peer, menu: MessageRef, kind: str, entity_id: int, field: str,
        *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    from piltover.db.enums import AdminBotState
    from piltover.db.models import Channel, Chat

    if kind == "user":
        user = await User.get_or_none(id=entity_id, bot=False, deleted=False)
        if user is None:
            return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)
        if field == "username" and user.system:
            return BotCallbackAnswer(message="Юзернейм системного аккаунта нельзя менять.", alert=True, cache_time=0)
    elif kind == "ch":
        if await Channel.get_or_none(id=entity_id, deleted=False) is None:
            return BotCallbackAnswer(message="Канал не найден.", alert=True, cache_time=0)
    elif kind == "gr":
        if await Chat.get_or_none(id=entity_id, deleted=False, migrated=False) is None:
            return BotCallbackAnswer(message="Группа не найдена.", alert=True, cache_time=0)
    else:
        return BotCallbackAnswer(message="Неизвестная сущность.", alert=True, cache_time=0)

    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_ENTITY_EDIT,
        f"{kind}:{field}:{entity_id}:{list_key}:{menu.id}".encode(),
    )
    await pages_extended.page_entity_edit_prompt(peer, menu, kind, entity_id, field, list_key=list_key)
    return BotCallbackAnswer(message="Отправьте новое значение в чат.", cache_time=0)


async def begin_bot_edit_input(
        peer: Peer, menu: MessageRef, bot_id: int, field: str, *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
    if bot_user is None:
        return BotCallbackAnswer(message="Бот не найден.", alert=True, cache_time=0)
    if field == "username" and bot_user.system:
        return BotCallbackAnswer(message="Имя пользователя системного бота нельзя изменить.", alert=True, cache_time=0)

    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_BOT_EDIT,
        f"{field}:{bot_id}:{list_key}:{menu.id}".encode(),
    )
    await pages_extended.page_bot_edit_prompt(peer, menu, bot_id, field, list_key=list_key)
    return BotCallbackAnswer(message="Отправьте новое значение в чат.", cache_time=0)


async def begin_transfer_owner_input(
        peer: Peer, menu: MessageRef, entity_kind: str, entity_id: int, *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    from piltover.db.enums import AdminBotState
    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_TRANSFER_OWNER,
        f"{entity_kind}:{entity_id}:{list_key}".encode(),
    )
    return BotCallbackAnswer(message="Отправьте ID нового владельца в чат.", alert=True, cache_time=0)


async def begin_custom_stars_input(
        peer: Peer, menu: MessageRef, user_id: int, *, list_key: str, admin_user_id: int,
) -> BotCallbackAnswer:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return BotCallbackAnswer(message="Пользователь не найден.", alert=True, cache_time=0)

    await AdminBotUserState.set_state(
        admin_user_id,
        AdminBotState.WAIT_STARS_AMOUNT,
        encode_stars_wait_data(user_id, list_key),
    )
    await pages.page_user_stars(peer, user_id, menu, list_key=list_key)
    return BotCallbackAnswer(
        message="Отправьте желаемый баланс звёзд числом в чат.",
        alert=True,
        cache_time=0,
    )