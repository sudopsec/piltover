from __future__ import annotations

from piltover.app.bot_handlers.adminbot.callback_data import (
    back_list_data,
    encode_list_key,
    stars_action,
    user_action,
    user_link,
)
from piltover.app.bot_handlers.adminbot.utils import (
    HOME,
    PAGE_SIZE,
    back_home_row,
    home_keyboard,
    list_keyboard,
    user_label,
)
from piltover.app.bot_handlers.typetestbot.common import edit_bot_message
from piltover.app.utils.admin_memberships import get_user_memberships
from piltover.app.utils.admin_sessions import get_user_sessions
from piltover.db.models import Channel, Chat, MessageRef, Peer, User, UserStarsBalance, Username
from piltover.tl import KeyboardButtonCallback, KeyboardButtonRow, ReplyInlineMarkup


async def page_home(peer: Peer, menu: MessageRef) -> MessageRef:
    text = (
        "🛡 Admin Panel\n\n"
        "Server administration. Choose a category below."
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
        KeyboardButtonCallback(text="« Users", data=back_list_data(list_key)),
        KeyboardButtonCallback(text="« Main menu", data=HOME),
    ])


async def page_users(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    users = list(
        await User.filter(bot=False, system=False, deleted=False).order_by("-id")
    )
    total = len(users)
    list_key = encode_list_key("u", page)
    if total == 0:
        return await edit_bot_message(menu, peer, "No users found.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:users",
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    list_key = encode_list_key("u", page)
    chunk = users[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    usernames = await _usernames_for_users(chunk)

    items = [
        (user_label(user, username=usernames.get(user.id)), user_link(user.id, list_key))
        for user in chunk
    ]
    text = f"Users ({total}). Tap to manage:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:users")
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_admins(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    users = list(await User.filter(admin=True, bot=False, deleted=False).order_by("-id"))
    total = len(users)
    if total == 0:
        return await edit_bot_message(menu, peer, "No admins found.", list_keyboard(
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
    text = f"Admins ({total}). Tap to manage:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:admins")
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_channels(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    channels = list(await Channel.filter(deleted=False).order_by("-id"))
    total = len(channels)
    if total == 0:
        return await edit_bot_message(menu, peer, "No channels.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:channels",
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    chunk = channels[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    items: list[tuple[str, bytes]] = []
    for channel in chunk:
        kind = "channel" if channel.channel else "supergroup"
        badge = " ✓" if channel.verified else ""
        label = f"[{kind}] {channel.name}{badge}"
        if channel.verified:
            data = f"adm:act:uv:ch:{channel.id}".encode()
        else:
            data = f"adm:act:v:ch:{channel.id}".encode()
        items.append((label[:64], data))

    text = f"Channels & supergroups ({total}). Tap to toggle verified:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:channels")
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_groups(peer: Peer, page: int, menu: MessageRef) -> MessageRef:
    chats = list(await Chat.filter(deleted=False, migrated=False).order_by("-id"))
    total = len(chats)
    if total == 0:
        return await edit_bot_message(menu, peer, "No basic groups.", list_keyboard(
            items=[], page=0, total_pages=1, page_prefix=b"adm:groups",
        ))

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    chunk = chats[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    items: list[tuple[str, bytes]] = []
    for chat in chunk:
        badge = " ✓" if chat.verified else ""
        label = f"[group] {chat.name}{badge}"
        if chat.verified:
            data = f"adm:act:uv:g:{chat.id}".encode()
        else:
            data = f"adm:act:v:g:{chat.id}".encode()
        items.append((label[:64], data))

    text = f"Basic groups ({total}). Tap to toggle verified:"
    keyboard = list_keyboard(items=items, page=page, total_pages=total_pages, page_prefix=b"adm:groups")
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

    text = (
        "📊 Server statistics\n\n"
        f"Users: {users}\n"
        f"Bots: {bots}\n"
        f"Admins: {admins}\n"
        f"Verified users: {verified_users}\n"
        f"Spam blocked: {spam_blocked}\n"
        f"Channels: {channels}\n"
        f"Supergroups: {supergroups}\n"
        f"Basic groups: {groups}"
    )
    keyboard = ReplyInlineMarkup(rows=[back_home_row()])
    return await edit_bot_message(menu, peer, text, keyboard)


async def page_user(peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0") -> MessageRef:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return await edit_bot_message(menu, peer, "User not found.", ReplyInlineMarkup(rows=[back_home_row()]))

    username = await user.get_raw_username()
    stars = await _stars_amount(user.id)
    sessions = await get_user_sessions(user.id)
    memberships = await get_user_memberships(user.id)

    lines = [
        f"👤 {user.first_name}" + (f" {user.last_name}" if user.last_name else ""),
        f"ID: {user.id}",
    ]
    if username:
        lines.append(f"Username: @{username}")
    if user.phone_number:
        lines.append(f"Phone: {user.phone_number}")
    lines.append(f"Admin: {'yes' if user.admin else 'no'}")
    lines.append(f"Verified: {'yes' if user.verified else 'no'}")
    lines.append(f"Spam blocked: {'yes' if user.spam_blocked else 'no'}")
    lines.append(f"Stars: ⭐ {stars}")
    lines.append(f"Sessions: {len(sessions)}")
    lines.append(f"Admin memberships: {len(memberships)}")

    rows: list[KeyboardButtonRow] = []
    if user.admin:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Revoke admin", data=user_action("unadmin", user.id, list_key)),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Grant admin", data=user_action("admin", user.id, list_key)),
        ]))

    if user.verified:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Remove checkmark", data=user_action("unverify", user.id, list_key)),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Grant checkmark", data=user_action("verify", user.id, list_key)),
        ]))

    if user.spam_blocked:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Remove spam block", data=user_action("unspam", user.id, list_key)),
        ]))
    else:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Apply spam block", data=user_action("spam", user.id, list_key)),
        ]))

    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="⭐ Stars", data=f"adm:user:stars:{user.id}:{list_key}".encode()),
        KeyboardButtonCallback(text="📱 Sessions", data=f"adm:user:sess:{user.id}:{list_key}".encode()),
    ]))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="📋 Memberships", data=f"adm:user:mem:{user.id}:0:{list_key}".encode()),
    ]))
    rows.append(_user_nav_row(list_key))

    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_user_stars(peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0") -> MessageRef:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return await edit_bot_message(menu, peer, "User not found.", ReplyInlineMarkup(rows=[back_home_row()]))

    stars = await _stars_amount(user.id)
    text = f"⭐ Stars for {user.first_name}\n\nCurrent balance: {stars}"

    rows = [
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="+25", data=stars_action("add", user.id, 25, list_key)),
            KeyboardButtonCallback(text="+100", data=stars_action("add", user.id, 100, list_key)),
            KeyboardButtonCallback(text="+500", data=stars_action("add", user.id, 500, list_key)),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Set 0", data=stars_action("set", user.id, 0, list_key)),
            KeyboardButtonCallback(text="Set 100", data=stars_action("set", user.id, 100, list_key)),
            KeyboardButtonCallback(text="Set 1000", data=stars_action("set", user.id, 1000, list_key)),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Custom amount", data=stars_action("custom", user.id, 0, list_key)),
        ]),
        KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="« Back to user", data=user_link(user.id, list_key)),
        ]),
    ]
    return await edit_bot_message(menu, peer, text, ReplyInlineMarkup(rows=rows))


async def page_user_sessions(peer: Peer, user_id: int, menu: MessageRef, *, list_key: str = "u0") -> MessageRef:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return await edit_bot_message(menu, peer, "User not found.", ReplyInlineMarkup(rows=[back_home_row()]))

    sessions = await get_user_sessions(user.id)
    lines = [f"📱 Sessions for {user.first_name} ({len(sessions)})", ""]
    if not sessions:
        lines.append("No active sessions.")
    else:
        for idx, session in enumerate(sessions[:10], start=1):
            lines.append(
                f"{idx}. {session.platform} / {session.device_model}\n"
                f"   {session.system_version} · {session.app_version}\n"
                f"   IP {session.ip}"
            )
        if len(sessions) > 10:
            lines.append(f"... and {len(sessions) - 10} more")

    rows: list[KeyboardButtonRow] = []
    if sessions:
        rows.append(KeyboardButtonRow(buttons=[
            KeyboardButtonCallback(text="Kick all sessions", data=user_action("kick", user.id, list_key)),
        ]))
    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Back to user", data=user_link(user.id, list_key)),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))


async def page_user_memberships(
        peer: Peer, user_id: int, page: int, menu: MessageRef, *, list_key: str = "u0",
) -> MessageRef:
    user = await User.get_or_none(id=user_id, bot=False, system=False, deleted=False)
    if user is None:
        return await edit_bot_message(menu, peer, "User not found.", ReplyInlineMarkup(rows=[back_home_row()]))

    memberships = await get_user_memberships(user.id)
    total = len(memberships)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = memberships[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [f"📋 Memberships for {user.first_name} ({total})", ""]
    if not chunk:
        lines.append("No admin groups, channels, or owned bots.")
    else:
        for item in chunk:
            lines.append(f"[{item.kind}/{item.role}] {item.title} (id {item.entity_id})")

    rows: list[KeyboardButtonRow] = []
    nav: list[KeyboardButtonCallback] = []
    if page > 0:
        nav.append(KeyboardButtonCallback(
            text="« Prev", data=f"adm:user:mem:{user.id}:{page - 1}:{list_key}".encode(),
        ))
    if page + 1 < total_pages:
        nav.append(KeyboardButtonCallback(
            text="Next »", data=f"adm:user:mem:{user.id}:{page + 1}:{list_key}".encode(),
        ))
    if nav:
        rows.append(KeyboardButtonRow(buttons=nav))

    rows.append(KeyboardButtonRow(buttons=[
        KeyboardButtonCallback(text="« Back to user", data=user_link(user.id, list_key)),
    ]))
    return await edit_bot_message(menu, peer, "\n".join(lines), ReplyInlineMarkup(rows=rows))