from __future__ import annotations

import piltover.app.utils.updates_manager as upd
from piltover.config import APP_CONFIG
from piltover.db.models import Channel, Chat, User, Username
from piltover.db.models.user_personal_channel import UserPersonalChannel
from piltover.exceptions import ErrorRpc
from piltover.app.utils.utils import is_username_valid, validate_username
from tortoise.expressions import F
from tortoise.transactions import in_transaction

CLEARABLE_USER_FIELDS = frozenset({"lastname", "username", "about", "phone"})
CLEARABLE_CHANNEL_FIELDS = frozenset({"about", "username"})
CLEARABLE_GROUP_FIELDS = frozenset({"about"})


async def _user_for_name_update(user: User) -> User:
    return await User.get(id=user.id).select_related("username")


def _error_from_rpc(exc: ErrorRpc) -> str:
    messages = {
        "CHAT_TITLE_EMPTY": "Название не может быть пустым.",
        "CHAT_ABOUT_TOO_LONG": "Описание слишком длинное (макс. 255).",
        "CHAT_NOT_MODIFIED": "Значение не изменилось.",
        "CHAT_ABOUT_NOT_MODIFIED": "Описание не изменилось.",
        "USERNAME_OCCUPIED": "Юзернейм уже занят.",
        "USERNAME_INVALID": "Неверный юзернейм.",
        "FIRSTNAME_INVALID": "Имя не может быть пустым.",
        "ABOUT_TOO_LONG": f"«О себе» слишком длинное (макс. {APP_CONFIG.user_bio_limit}).",
    }
    return messages.get(exc.error_message, exc.error_message)


async def _set_user_username(user: User, username: str | None) -> str | None:
    if user.system:
        return "Юзернейм системного аккаунта нельзя менять."

    username = (username or "").lstrip("@").lower().strip()
    if not username:
        async with in_transaction():
            if user.username is not None:
                await user.username.delete()
                user._username = None
                user.version += 1
                await user.save(update_fields=["version"])
        await upd.update_user_name(await _user_for_name_update(user))
        return None

    if not is_username_valid(username):
        return "Неверный юзернейм (1–32 символа: a-z, 0-9, _)."

    try:
        validate_username(username)
    except ErrorRpc as exc:
        return _error_from_rpc(exc)

    async with in_transaction():
        if await Username.filter(username__iexact=username).exclude(user_id=user.id).exists():
            return "Юзернейм уже занят."
        existing = await Username.get_or_none(user_id=user.id)
        if existing is None:
            user._username = await Username.create(user=user, username=username)
        else:
            existing.username = username
            await existing.save(update_fields=["username"])
            user._username = existing
        user.version += 1
        await user.save(update_fields=["version"])

    await upd.update_user_name(await _user_for_name_update(user))
    return None


async def _set_channel_username(channel: Channel, username: str | None) -> str | None:
    username = (username or "").lstrip("@").lower().strip()
    current = await Username.get_or_none(channel_id=channel.id)

    if not username:
        if current is not None:
            await current.delete()
            await UserPersonalChannel.filter(channel=channel).delete()
            channel._username = None
            await Channel.filter(id=channel.id).update(version=F("version") + 1)
            await channel.refresh_from_db(["version"])
            await upd.update_channel(channel)
        return None

    if not is_username_valid(username):
        return "Неверный юзернейм (1–32 символа: a-z, 0-9, _)."

    try:
        validate_username(username)
    except ErrorRpc as exc:
        return _error_from_rpc(exc)

    if await Username.filter(username__iexact=username).exclude(channel_id=channel.id).exists():
        return "Юзернейм уже занят."

    if current is None:
        channel._username = await Username.create(channel=channel, username=username)
    else:
        current.username = username
        await current.save(update_fields=["username"])
        channel._username = current

    await Channel.filter(id=channel.id).update(version=F("version") + 1)
    await channel.refresh_from_db(["version"])
    await upd.update_channel(channel)
    return None


async def apply_user_field_value(
        user: User, field: str, text: str, *, clear: bool = False,
) -> str | None:
    value = text.strip()

    if field == "name":
        if clear or not value:
            return "Имя не может быть пустым."
        if len(value) > 128:
            return "Имя слишком длинное (макс. 128)."
        user.first_name = value
        user.version += 1
        await user.save(update_fields=["first_name", "version"])
        await upd.update_user_name(await _user_for_name_update(user))
        return None

    if field == "lastname":
        if clear:
            user.last_name = None
        else:
            if len(value) > 128:
                return "Фамилия слишком длинная (макс. 128)."
            user.last_name = value or None
        user.version += 1
        await user.save(update_fields=["last_name", "version"])
        await upd.update_user_name(await _user_for_name_update(user))
        return None

    if field == "username":
        if clear:
            return await _set_user_username(user, None)
        return await _set_user_username(user, value)

    if field == "about":
        if clear:
            user.about = None
        else:
            if len(value) > APP_CONFIG.user_bio_limit:
                return f"«О себе» слишком длинное (макс. {APP_CONFIG.user_bio_limit})."
            user.about = value or None
        user.version += 1
        await user.save(update_fields=["about", "version"])
        await upd.update_user(user)
        return None

    if field == "phone":
        if clear:
            user.phone_number = None
        else:
            phone = "".join(ch for ch in value if ch.isdigit())
            if len(phone) < 5:
                return "Неверный номер телефона."
            if await User.filter(phone_number=phone).exclude(id=user.id).exists():
                return "Номер уже занят."
            user.phone_number = phone
        user.version += 1
        await user.save(update_fields=["phone_number", "version"])
        await upd.update_user(user)
        return None

    return "Неизвестное поле."


async def apply_channel_field_value(
        channel: Channel, field: str, text: str, *, clear: bool = False,
) -> str | None:
    value = text.strip()

    if field == "name":
        if clear or not value:
            return "Название не может быть пустым."
        try:
            await channel.update(title=value)
        except ErrorRpc as exc:
            return _error_from_rpc(exc)
        await upd.update_channel(channel)
        return None

    if field == "about":
        try:
            await channel.update(description="" if clear else value)
        except ErrorRpc as exc:
            return _error_from_rpc(exc)
        await upd.update_channel(channel)
        return None

    if field == "username":
        if clear:
            return await _set_channel_username(channel, None)
        return await _set_channel_username(channel, value)

    return "Неизвестное поле."


async def apply_group_field_value(
        chat: Chat, field: str, text: str, *, clear: bool = False,
) -> str | None:
    value = text.strip()

    if field == "name":
        if clear or not value:
            return "Название не может быть пустым."
        try:
            await chat.update(title=value)
        except ErrorRpc as exc:
            return _error_from_rpc(exc)
        await upd.update_chat(chat)
        return None

    if field == "about":
        try:
            await chat.update(description="" if clear else value)
        except ErrorRpc as exc:
            return _error_from_rpc(exc)
        await upd.update_chat(chat)
        return None

    return "Неизвестное поле."