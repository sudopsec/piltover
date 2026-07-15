from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.app.bot_handlers.adminbot import actions_server, pages, pages_extended
from piltover.app.bot_handlers.adminbot.callback_data import decode_stars_wait_data, LIST_KEY_DEFAULT
from piltover.app.bot_handlers.adminbot.utils import send_bot_message
from piltover.app.bot_handlers.interaction_handler import BotInteractionHandler
from piltover.app.utils.admin_channel_ops import transfer_channel_owner, transfer_chat_owner
from piltover.app.utils.admin_bot_edit import apply_bot_field_value
from piltover.app.utils.admin_entity_edit import (
    apply_channel_field_value, apply_group_field_value, apply_user_field_value,
)
from piltover.app.utils.admin_lookup import (
    resolve_bot_query, resolve_channel_query, resolve_chat_query, resolve_user_query, search_users_substring,
)
from piltover.app.utils.admin_search import SearchFilters
from piltover.app.utils.stars_manager import set_stars_balance
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, MessageRef, Peer, User
from piltover.exceptions import ErrorRpc


class AdminBotTextHandler(BotInteractionHandler[AdminBotState, AdminBotUserState]):
    def __init__(self) -> None:
        super().__init__(AdminBotUserState)
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_STARS_AMOUNT)
            .do(self._custom_stars_amount)
            .delete_state()
            .register()
        )
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_SEARCH)
            .do(self._search_query)
            .delete_state()
            .register()
        )
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_TRANSFER_OWNER)
            .do(self._transfer_owner)
            .delete_state()
            .register()
        )
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_BOT_EDIT)
            .do(self._bot_edit)
            .delete_state()
            .register()
        )
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_ENTITY_EDIT)
            .do(self._entity_edit)
            .delete_state()
            .register()
        )
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_SYSTEM_TARGET)
            .do(self._system_target)
            .register()
        )
        (
            self.text()
            .set_send_message_func(send_bot_message)
            .when(state=AdminBotState.WAIT_SYSTEM_TEXT)
            .do(self._system_text)
            .delete_state()
            .register()
        )

    @staticmethod
    async def _system_target(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef | None:
        return await actions_server.handle_system_target_input(peer, message, state)

    @staticmethod
    async def _system_text(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef | None:
        return await actions_server.handle_system_text_input(peer, message, state)

    @staticmethod
    async def _custom_stars_amount(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef:
        text = message.content.message
        if text is None:
            return await send_bot_message(peer, "Отправьте число.")

        try:
            amount = int(text.strip())
        except ValueError:
            return await send_bot_message(peer, "Неверное число. Отправьте целый баланс звёзд.")

        if amount < 0:
            return await send_bot_message(peer, "Сумма должна быть нулём или больше.")

        target_user_id, list_key = decode_stars_wait_data(state.data)
        target = await User.get_or_none(id=target_user_id, bot=False, system=False, deleted=False)
        if target is None:
            return await send_bot_message(peer, "Целевой пользователь не найден.")

        try:
            balance = await set_stars_balance(
                target_user_id,
                amount,
                title="Admin set",
                description=f"Custom balance {amount} stars via @admin",
            )
        except ErrorRpc as exc:
            return await send_bot_message(peer, exc.error_message)

        await upd.update_stars_balance(target_user_id, balance.to_stars_amount())
        return await send_bot_message(peer, f"Баланс {amount} ⭐ для {target.first_name}.")

    @staticmethod
    async def _search_query(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef:
        query = (message.content.message or "").strip()
        filters = SearchFilters.decode(state.data)

        if filters.kind == "user":
            user = await resolve_user_query(
                query,
                include_deleted=filters.include_deleted,
                include_system=filters.show_system,
            )
            if user is None:
                matches = await search_users_substring(
                    query,
                    include_deleted=filters.include_deleted,
                    include_system=filters.show_system,
                )
                if len(matches) == 1:
                    user = matches[0]
                elif len(matches) > 1:
                    names = ", ".join(f"{u.first_name} ({u.id})" for u in matches[:8])
                    suffix = "…" if len(matches) > 8 else ""
                    return await send_bot_message(peer, f"Несколько совпадений: {names}{suffix}")
            if user is None:
                return await send_bot_message(peer, "Пользователь не найден.")
            if user.deleted:
                return await pages_extended.page_deleted_user(peer, user.id, message, list_key="d0")
            return await pages.page_user(peer, user.id, message, list_key="u0", overlay=True)

        if filters.kind == "ch":
            channel = await resolve_channel_query(query, channel_kind=filters.channel_kind)
            if channel is None:
                return await send_bot_message(peer, "Канал не найден.")
            return await pages_extended.page_channel(
                peer, channel.id, message, list_key="c0", new_message=True,
            )

        if filters.kind == "gr":
            chat = await resolve_chat_query(query)
            if chat is None:
                return await send_bot_message(peer, "Группа не найдена.")
            return await pages_extended.page_group(
                peer, chat.id, message, list_key="g0", new_message=True,
            )

        if filters.kind == "bot":
            bot_user = await resolve_bot_query(query, include_system=filters.show_system)
            if bot_user is None:
                return await send_bot_message(peer, "Бот не найден.")
            return await pages_extended.page_bot(peer, bot_user.id, message, list_key="b0", new_message=True)

        return await send_bot_message(peer, "Неизвестный тип поиска.")

    @staticmethod
    async def _resolve_bot_edit_menu(menu_id: int | None) -> MessageRef | None:
        if menu_id is None:
            return None
        return await MessageRef.get_or_none(id=menu_id).select_related("content", "peer")

    @staticmethod
    async def _resolve_entity_edit_menu(menu_id: int | None) -> MessageRef | None:
        if menu_id is None:
            return None
        return await MessageRef.get_or_none(id=menu_id).select_related("content", "peer")

    @staticmethod
    async def _entity_edit(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef:
        text = (message.content.message or "").strip()
        payload = (state.data or b"").decode()
        parts = payload.split(":")
        if len(parts) < 4:
            return await send_bot_message(peer, "Сессия истекла.")

        kind, field, entity_id_str, list_key = parts[0], parts[1], parts[2], parts[3]
        menu_id = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
        entity_id = int(entity_id_str)
        menu_ref = await AdminBotTextHandler._resolve_entity_edit_menu(menu_id)
        clear = text in {"-", "—", "clear", "none", "empty", "очистить", "пусто"}

        if kind == "user":
            user = await User.get_or_none(id=entity_id, bot=False, deleted=False)
            if user is None:
                return await send_bot_message(peer, "Пользователь не найден.")
            error = await apply_user_field_value(user, field, text, clear=clear)
            if error is not None:
                return await send_bot_message(peer, error)
            if menu_ref is not None:
                return await pages_extended.page_user_settings(peer, entity_id, menu_ref, list_key=list_key)
            return await pages_extended.page_user_settings(
                peer, entity_id, message, list_key=list_key, new_message=True,
            )

        if kind == "ch":
            from piltover.db.models import Channel
            channel = await Channel.get_or_none(id=entity_id, deleted=False)
            if channel is None:
                return await send_bot_message(peer, "Канал не найден.")
            error = await apply_channel_field_value(channel, field, text, clear=clear)
            if error is not None:
                return await send_bot_message(peer, error)
            if menu_ref is not None:
                return await pages_extended.page_channel_settings(peer, entity_id, menu_ref, list_key=list_key)
            return await pages_extended.page_channel_settings(
                peer, entity_id, message, list_key=list_key, new_message=True,
            )

        if kind == "gr":
            from piltover.db.models import Chat
            chat = await Chat.get_or_none(id=entity_id, deleted=False, migrated=False)
            if chat is None:
                return await send_bot_message(peer, "Группа не найдена.")
            error = await apply_group_field_value(chat, field, text, clear=clear)
            if error is not None:
                return await send_bot_message(peer, error)
            if menu_ref is not None:
                return await pages_extended.page_group_settings(peer, entity_id, menu_ref, list_key=list_key)
            return await pages_extended.page_group_settings(
                peer, entity_id, message, list_key=list_key, new_message=True,
            )

        return await send_bot_message(peer, "Неизвестная сущность.")

    @staticmethod
    async def _bot_edit(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef:
        text = (message.content.message or "").strip()
        payload = (state.data or b"").decode()
        parts = payload.split(":")
        if len(parts) < 3:
            return await send_bot_message(peer, "Сессия истекла.")

        field, bot_id_str, list_key = parts[0], parts[1], parts[2]
        menu_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
        bot_id = int(bot_id_str)
        menu_ref = await AdminBotTextHandler._resolve_bot_edit_menu(menu_id)
        bot_user = await User.get_or_none(id=bot_id, bot=True, deleted=False)
        if bot_user is None:
            return await send_bot_message(peer, "Бот не найден.")

        clear = text in {"-", "—", "clear", "none", "empty", "очистить", "пусто"}
        error = await apply_bot_field_value(bot_user, field, text, clear=clear)
        if error is not None:
            return await send_bot_message(peer, error)

        if menu_ref is not None:
            return await pages_extended.page_bot_settings(peer, bot_id, menu_ref, list_key=list_key)
        return await pages_extended.page_bot_settings(
            peer, bot_id, message, list_key=list_key, new_message=True,
        )

    @staticmethod
    async def _transfer_owner(peer: Peer, message: MessageRef, state: AdminBotUserState) -> MessageRef:
        text = (message.content.message or "").strip()
        if not text.isdigit():
            return await send_bot_message(peer, "Отправьте числовой id пользователя.")

        new_owner_id = int(text)
        payload = (state.data or b"").decode()
        parts = payload.split(":")
        if len(parts) < 3:
            return await send_bot_message(peer, "Сессия истекла.")

        kind, entity_id_str, list_key = parts[0], parts[1], parts[2]
        entity_id = int(entity_id_str)

        target = await User.get_or_none(id=new_owner_id, deleted=False, bot=False)
        if target is None:
            return await send_bot_message(peer, "Целевой пользователь не найден.")

        try:
            if kind == "ch":
                from piltover.db.models import Channel
                channel = await Channel.get_or_none(id=entity_id, deleted=False)
                if channel is None:
                    return await send_bot_message(peer, "Канал не найден.")
                await transfer_channel_owner(channel, new_owner_id)
                return await pages_extended.page_channel(peer, entity_id, message, list_key=list_key)
            if kind == "gr":
                from piltover.db.models import Chat
                chat = await Chat.get_or_none(id=entity_id, deleted=False)
                if chat is None:
                    return await send_bot_message(peer, "Группа не найдена.")
                await transfer_chat_owner(chat, new_owner_id)
                return await pages_extended.page_group(peer, entity_id, message, list_key=list_key)
        except ValueError:
            return await send_bot_message(peer, "Не удалось передать — пользователь должен быть участником.")

        return await send_bot_message(peer, "Неизвестная сущность.")