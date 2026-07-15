from __future__ import annotations

from urllib.parse import urlparse

import piltover.app.utils.updates_manager as upd
from piltover.app.utils.utils import is_username_valid
from piltover.db.models import BotInfo, User, Username
from tortoise.expressions import F
from tortoise.transactions import in_transaction

CLEARABLE_BOT_FIELDS = frozenset({"lastname", "about", "desc", "privacy"})


async def ensure_bot_info(bot_id: int) -> BotInfo:
    info = await BotInfo.get_or_none(user_id=bot_id)
    if info is not None:
        return info
    user = await User.get(id=bot_id)
    return await BotInfo.create(user=user)


async def set_bot_first_name(bot_user: User, first_name: str) -> None:
    bot_user.first_name = first_name
    await bot_user.save(update_fields=["first_name", "version"])
    await bot_user.inc_version()
    await upd.update_user(bot_user)


async def set_bot_last_name(bot_user: User, last_name: str | None) -> None:
    bot_user.last_name = last_name or None
    await bot_user.save(update_fields=["last_name", "version"])
    await bot_user.inc_version()
    await upd.update_user(bot_user)


async def set_bot_about(bot_user: User, about: str | None) -> None:
    bot_user.about = about or None
    await bot_user.save(update_fields=["about", "version"])
    await bot_user.inc_version()
    await upd.update_user(bot_user)


async def set_bot_description(bot_user: User, description: str | None) -> None:
    await ensure_bot_info(bot_user.id)
    await User.filter(id=bot_user.id).update(version=F("version") + 1)
    await BotInfo.filter(user_id=bot_user.id).update(
        description=description or None,
        version=F("version") + 1,
    )
    await bot_user.refresh_from_db()
    await upd.update_user(bot_user)


async def set_bot_username(bot_user: User, username: str | None) -> None:
    if bot_user.system:
        raise ValueError("System bot username cannot be changed.")

    if username is None:
        raise ValueError("Bot username cannot be removed.")

    username = username.lstrip("@").lower().strip()
    if not username:
        raise ValueError("Bot username cannot be removed.")
    if not is_username_valid(username):
        raise ValueError("Invalid username (1–32 chars: a-z, 0-9, _).")

    async with in_transaction():
        existing = await Username.get_or_none(user_id=bot_user.id)
        if existing and existing.username.lower() == username:
            return
        if await Username.filter(username__iexact=username).exclude(user_id=bot_user.id).exists():
            raise ValueError("Username is already taken.")
        if existing is None:
            bot_user._username = await Username.create(user=bot_user, username=username)
        else:
            existing.username = username
            await existing.save(update_fields=["username"])
            bot_user._username = existing

        bot_user.version += 1
        await bot_user.save(update_fields=["version"])

    await upd.update_user_name(bot_user)


async def apply_bot_field_value(
        bot_user: User, field: str, text: str, *, clear: bool = False,
) -> str | None:
    """Apply a bot profile field. Returns an error message or None on success."""
    value = text.strip()

    if field == "name":
        if clear or not value:
            return "Имя не может быть пустым."
        if len(value) > 64:
            return "Имя слишком длинное (макс. 64)."
        await set_bot_first_name(bot_user, value)
        return None

    if field == "lastname":
        if clear:
            await set_bot_last_name(bot_user, None)
            return None
        if len(value) > 64:
            return "Фамилия слишком длинная (макс. 64)."
        await set_bot_last_name(bot_user, value)
        return None

    if field == "username":
        if bot_user.system:
            return "Юзернейм системного бота нельзя менять."
        if clear or not value:
            return "Юзернейм бота нельзя удалить."
        try:
            await set_bot_username(bot_user, value)
        except ValueError as exc:
            return str(exc)
        return None

    if field == "about":
        if clear:
            await set_bot_about(bot_user, None)
            return None
        if len(value) > 120:
            return "«О боте» слишком длинное (макс. 120)."
        await set_bot_about(bot_user, value)
        return None

    if field == "desc":
        if clear:
            await set_bot_description(bot_user, None)
            return None
        if len(value) > 120:
            return "Описание слишком длинное (макс. 120)."
        await set_bot_description(bot_user, value)
        return None

    if field == "privacy":
        if clear:
            await set_bot_privacy_policy(bot_user, None)
            return None
        try:
            await set_bot_privacy_policy(bot_user, value)
        except ValueError as exc:
            return str(exc)
        return None

    return "Неизвестное поле."


async def set_bot_privacy_policy(bot_user: User, url: str | None) -> None:
    if url:
        parsed = urlparse(url)
        if not parsed.netloc or parsed.scheme != "https":
            raise ValueError("Privacy policy must be an https URL.")
        if len(url) > 240:
            raise ValueError("URL is too long (max 240).")

    await ensure_bot_info(bot_user.id)
    await User.filter(id=bot_user.id).update(version=F("version") + 1)
    await BotInfo.filter(user_id=bot_user.id).update(
        privacy_policy_url=url or None,
        version=F("version") + 1,
    )
    await bot_user.refresh_from_db()
    await upd.update_user(bot_user)