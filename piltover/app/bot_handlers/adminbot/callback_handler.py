from __future__ import annotations

from piltover.app.bot_handlers.adminbot import actions, actions_server, pages, pages_extended, pages_server
from piltover.app.bot_handlers.adminbot.callback_data import split_list_key
from piltover.db.enums import AdminBotState
from piltover.db.models import AdminBotUserState, MessageRef, Peer
from piltover.tl.types.messages import BotCallbackAnswer


async def _clear_system_input_state(admin_user_id: int) -> None:
    await AdminBotUserState.filter(
        user_id=admin_user_id,
        state__in=[AdminBotState.WAIT_SYSTEM_TARGET, AdminBotState.WAIT_SYSTEM_TEXT],
    ).delete()


def _parse_user_page(data: bytes) -> tuple[int, str]:
    body, list_key = split_list_key(data)
    text = body.decode()
    if text.startswith("adm:user:stars:"):
        return int(text.split(":")[3]), list_key
    if text.startswith("adm:user:sess:"):
        return int(text.split(":")[3]), list_key
    if text.startswith("adm:user:mem:"):
        return int(text.split(":")[3]), list_key
    if text.startswith("adm:user:"):
        parts = text.split(":")
        return int(parts[2]), list_key
    raise ValueError(f"Unexpected user page callback: {text}")


def _parse_parts(data: bytes) -> tuple[list[str], str]:
    body, list_key = split_list_key(data)
    return body.decode().split(":"), list_key


async def adminbot_callback_query_handler(
        peer: Peer, message: MessageRef, data: bytes,
) -> BotCallbackAnswer | None:
    if data == b"adm:home":
        await AdminBotUserState.filter(user_id=peer.owner_id).delete()
        await pages.page_home(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:stats":
        await pages.page_stats(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:server":
        await _clear_system_input_state(peer.owner_id)
        await pages_server.page_server_menu(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:cfg":
        await _clear_system_input_state(peer.owner_id)
        await pages_server.page_server_config(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:fun":
        await _clear_system_input_state(peer.owner_id)
        await pages_server.page_server_fun(peer, message)
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:notify":
        return await actions_server.begin_notify_target(peer, message, admin_user_id=peer.owner_id)

    if data == b"adm:notify:all":
        return await actions_server.begin_notify_broadcast(peer, message, admin_user_id=peer.owner_id)

    if data == b"adm:srvnotif":
        return await actions_server.begin_srvnotif_menu(peer, message, admin_user_id=peer.owner_id)

    if data.startswith(b"adm:srvnotif:"):
        suffix = data[13:]
        if suffix.startswith(b"all:"):
            popup = suffix[4:] == b"1"
            return await actions_server.begin_srvnotif_broadcast(
                peer, message, popup=popup, admin_user_id=peer.owner_id,
            )
        popup = suffix == b"1"
        return await actions_server.begin_srvnotif_target(
            peer, message, popup=popup, admin_user_id=peer.owner_id,
        )

    if data == b"adm:fun:code":
        return await actions_server.begin_fun_target(peer, message, kind="code", admin_user_id=peer.owner_id)

    if data == b"adm:fun:popup":
        return await actions_server.fun_test_popup_action(peer, message)

    if data.startswith(b"adm:cfg:"):
        key = data[8:].decode()
        return await actions_server.toggle_config_action(peer, message, key)

    if data.startswith(b"adm:findf:"):
        flag = data[10:].decode()
        return await actions.toggle_search_filter(peer, message, flag, admin_user_id=peer.owner_id)

    if data.startswith(b"adm:find:"):
        kind = data[9:].decode()
        return await actions.begin_search_input(peer, message, kind, admin_user_id=peer.owner_id)

    if data.startswith(b"adm:users:sys:"):
        await pages.page_users(peer, int(data[14:]), message, show_system=True)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:users:"):
        await pages.page_users(peer, int(data[10:]), message, show_system=False)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:admins:"):
        await pages.page_admins(peer, int(data[11:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:channels:"):
        await pages.page_channels(peer, int(data[13:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:groups:"):
        await pages.page_groups(peer, int(data[11:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:del:") and not data.startswith(b"adm:delu:"):
        await pages_extended.page_deleted_users(peer, int(data[8:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:delu:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_deleted_user(peer, int(parts[2]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:bots:sys:"):
        await pages_extended.page_bots(peer, int(data[13:]), message, show_system=True)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:bots:"):
        await pages_extended.page_bots(peer, int(data[9:]), message, show_system=False)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:bot:open:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[3])
        await pages_extended.page_bot(peer, bot_id, message, list_key=list_key, overlay=True)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:bot:empty:"):
        parts, list_key = _parse_parts(data)
        return await actions.clear_bot_field(
            peer, message, int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:bot:edit:"):
        parts, list_key = _parse_parts(data)
        return await actions.begin_bot_edit_input(
            peer, message, int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:bot:token:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_bot_token(peer, int(parts[3]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:bot:set:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_bot_settings(peer, int(parts[3]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:bot:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_bot(peer, int(parts[2]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:reports:"):
        await pages_extended.page_reports(peer, int(data[12:]), message)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:report:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_report(peer, int(parts[2]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:ch:mem:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_channel_members(
            peer, int(parts[3]), int(parts[4]), message, list_key=list_key,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:ch:adm:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_channel_admins(
            peer, int(parts[3]), int(parts[4]), message, list_key=list_key,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:ch:own:"):
        parts, list_key = _parse_parts(data)
        return await actions.begin_transfer_owner_input(
            peer, message, "ch", int(parts[3]), list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:ch:empty:"):
        parts, list_key = _parse_parts(data)
        return await actions.clear_entity_field(
            peer, message, "ch", int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:ch:edit:"):
        parts, list_key = _parse_parts(data)
        return await actions.begin_entity_edit_input(
            peer, message, "ch", int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:ch:set:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_channel_settings(peer, int(parts[3]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:ch:open:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_channel(
            peer, int(parts[3]), message, list_key=list_key, new_message=True,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:ch:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_channel(
            peer, int(parts[2]), message, list_key=list_key, new_message=True,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:gr:mem:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_group_members(
            peer, int(parts[3]), int(parts[4]), message, list_key=list_key,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:gr:adm:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_group_admins(
            peer, int(parts[3]), int(parts[4]), message, list_key=list_key,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:gr:own:"):
        parts, list_key = _parse_parts(data)
        return await actions.begin_transfer_owner_input(
            peer, message, "gr", int(parts[3]), list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:gr:empty:"):
        parts, list_key = _parse_parts(data)
        return await actions.clear_entity_field(
            peer, message, "gr", int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:gr:edit:"):
        parts, list_key = _parse_parts(data)
        return await actions.begin_entity_edit_input(
            peer, message, "gr", int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:gr:set:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_group_settings(peer, int(parts[3]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:gr:open:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_group(
            peer, int(parts[3]), message, list_key=list_key, new_message=True,
        )
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:gr:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_group(
            peer, int(parts[2]), message, list_key=list_key, new_message=True,
        )
        return BotCallbackAnswer(cache_time=0)

    if data == b"adm:act:hide":
        return await actions.hide_message_action(peer, message)

    if data.startswith(b"adm:user:open:"):
        body, list_key = split_list_key(data)
        user_id = int(body.decode().split(":")[3])
        await pages.page_user(peer, user_id, message, list_key=list_key, overlay=True)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:empty:"):
        parts, list_key = _parse_parts(data)
        return await actions.clear_entity_field(
            peer, message, "user", int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:user:edit:"):
        parts, list_key = _parse_parts(data)
        return await actions.begin_entity_edit_input(
            peer, message, "user", int(parts[4]), parts[3], list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:user:set:"):
        parts, list_key = _parse_parts(data)
        await pages_extended.page_user_settings(peer, int(parts[3]), message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:stars:"):
        user_id, list_key = _parse_user_page(data)
        await pages.page_user_stars(peer, user_id, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:sess:"):
        user_id, list_key = _parse_user_page(data)
        await pages.page_user_sessions(peer, user_id, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:mem:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        user_id = int(parts[3])
        page = int(parts[4])
        await pages.page_user_memberships(peer, user_id, page, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:user:"):
        user_id, list_key = _parse_user_page(data)
        await pages.page_user(peer, user_id, message, list_key=list_key)
        return BotCallbackAnswer(cache_time=0)

    if data.startswith(b"adm:act:stars:add:"):
        body, list_key = split_list_key(data)
        _, _, _, _, user_part, amount_part = body.decode().split(":", 5)
        return await actions.grant_user_stars(peer, message, int(user_part), int(amount_part), list_key=list_key)

    if data.startswith(b"adm:act:stars:set:"):
        body, list_key = split_list_key(data)
        _, _, _, _, user_part, amount_part = body.decode().split(":", 5)
        return await actions.set_user_stars(peer, message, int(user_part), int(amount_part), list_key=list_key)

    if data.startswith(b"adm:act:stars:custom:"):
        body, list_key = split_list_key(data)
        user_id = int(body.decode().split(":")[4])
        return await actions.begin_custom_stars_input(
            peer, message, user_id, list_key=list_key, admin_user_id=peer.owner_id,
        )

    if data.startswith(b"adm:act:verify:bot:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[4])
        return await actions.toggle_bot_verified(peer, message, bot_id, True, list_key=list_key)

    if data.startswith(b"adm:act:unverify:bot:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[4])
        return await actions.toggle_bot_verified(peer, message, bot_id, False, list_key=list_key)

    if data.startswith(b"adm:act:system:bot:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[4])
        return await actions.toggle_bot_system(peer, message, bot_id, True, list_key=list_key)

    if data.startswith(b"adm:act:unsystemok:bot:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[4])
        return await actions.toggle_bot_system(
            peer, message, bot_id, False, list_key=list_key, confirm=True,
        )

    if data.startswith(b"adm:act:unsystem:bot:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[4])
        return await actions.toggle_bot_system(peer, message, bot_id, False, list_key=list_key)

    if data.startswith(b"adm:act:revtoken:bot:"):
        body, list_key = split_list_key(data)
        bot_id = int(body.decode().split(":")[4])
        return await actions.revoke_bot_token_action(peer, message, bot_id, list_key=list_key)

    if data.startswith(b"adm:act:delbot:"):
        body, list_key = split_list_key(data)
        return await actions.delete_bot_action(peer, message, int(body.decode().split(":")[3]), list_key=list_key)

    if data.startswith(b"adm:act:delch:"):
        body, list_key = split_list_key(data)
        return await actions.delete_channel_action(peer, message, int(body.decode().split(":")[3]), list_key=list_key)

    if data.startswith(b"adm:act:deluser:"):
        body, list_key = split_list_key(data)
        return await actions.delete_user_action(peer, message, int(body.decode().split(":")[3]), list_key=list_key)

    if data.startswith(b"adm:act:restore:"):
        body, list_key = split_list_key(data)
        return await actions.restore_deleted_account_action(
            peer, message, int(body.decode().split(":")[3]), list_key=list_key,
        )

    if data.startswith(b"adm:act:kickch:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.kick_channel_member_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:kickgr:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.kick_group_member_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:admch:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.promote_channel_admin_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:admgr:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.promote_group_admin_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:revrep:"):
        body, list_key = split_list_key(data)
        return await actions.review_report_action(peer, message, int(body.decode().split(":")[3]), list_key=list_key)

    if data.startswith(b"adm:act:admin:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_admin(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:unadmin:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_admin(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:verify:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_verified(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:unverify:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_verified(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:unsupport:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_support(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:support:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_support(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:spamrep:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.spam_from_report_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:spamauthrep:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.spam_author_from_report_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:banrep:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.ban_user_from_report_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:banbotrep:"):
        body, list_key = split_list_key(data)
        parts = body.decode().split(":")
        return await actions.ban_bot_from_report_action(
            peer, message, int(parts[3]), int(parts[4]), list_key=list_key,
        )

    if data.startswith(b"adm:act:spam:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_spam_block(peer, message, int(body.decode().split(":")[3]), True, list_key=list_key)

    if data.startswith(b"adm:act:unspam:"):
        body, list_key = split_list_key(data)
        return await actions.toggle_user_spam_block(peer, message, int(body.decode().split(":")[3]), False, list_key=list_key)

    if data.startswith(b"adm:act:kick:"):
        body, list_key = split_list_key(data)
        return await actions.kick_user_sessions_action(peer, message, int(body.decode().split(":")[3]), list_key=list_key)

    if data.startswith(b"adm:act:v:ch:"):
        body, list_key = split_list_key(data)
        channel_id = int(body.decode().split(":")[4])
        return await actions.toggle_channel_verified(peer, message, channel_id, True, list_key=list_key)

    if data.startswith(b"adm:act:uv:ch:"):
        body, list_key = split_list_key(data)
        channel_id = int(body.decode().split(":")[4])
        return await actions.toggle_channel_verified(peer, message, channel_id, False, list_key=list_key)

    if data.startswith(b"adm:act:v:g:"):
        body, list_key = split_list_key(data)
        chat_id = int(body.decode().split(":")[4])
        return await actions.toggle_chat_verified(peer, message, chat_id, True, list_key=list_key)

    if data.startswith(b"adm:act:uv:g:"):
        body, list_key = split_list_key(data)
        chat_id = int(body.decode().split(":")[4])
        return await actions.toggle_chat_verified(peer, message, chat_id, False, list_key=list_key)

    return None