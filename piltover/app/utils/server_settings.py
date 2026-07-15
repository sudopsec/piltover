from __future__ import annotations

from piltover.db.models import ServerSettings
from piltover.exceptions import ErrorRpc

_CONFIG_FIELDS = (
    "reports_enabled",
    "bot_creation_enabled",
    "group_creation_enabled",
    "channel_creation_enabled",
    "phone_calls_enabled",
    "verifybot_enabled",
    "stars_bot_enabled",
)

_BOT_SETTING_BY_USERNAME = {
    "verifybot": "verifybot_enabled",
    "stars": "stars_bot_enabled",
    "stars_pay": "stars_bot_enabled",
}


async def get_server_settings() -> ServerSettings:
    settings, _ = await ServerSettings.get_or_create(id=1)
    return settings


async def toggle_server_setting(field: str) -> ServerSettings | None:
    if field not in _CONFIG_FIELDS:
        return None
    settings = await get_server_settings()
    current = getattr(settings, field)
    setattr(settings, field, not current)
    await settings.save(update_fields=[field])
    return settings


async def require_server_feature(field: str, *, error_code: int = 403, error_message: str) -> None:
    settings = await get_server_settings()
    if not getattr(settings, field):
        raise ErrorRpc(error_code=error_code, error_message=error_message)


async def is_bot_enabled(username: str) -> bool:
    field = _BOT_SETTING_BY_USERNAME.get(username)
    if field is None:
        return True
    settings = await get_server_settings()
    return bool(getattr(settings, field))