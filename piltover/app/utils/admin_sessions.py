from __future__ import annotations

from piltover.db.models import TempAuthKey, UserAuthorization
from piltover.session import SessionManager
from piltover.tl import UpdatesTooLong


async def kick_all_user_sessions(user_id: int) -> int:
    auths = list(await UserAuthorization.filter(user_id=user_id).only("id", "key_id"))
    if not auths:
        return 0

    keys = [auth.key_id for auth in auths]
    temp_keys = list(await TempAuthKey.filter(perm_key_id__in=keys).values_list("id", flat=True))
    keys.extend(temp_keys)

    await UserAuthorization.filter(id__in=[auth.id for auth in auths]).delete()

    if keys:
        await SessionManager.send(UpdatesTooLong(), key_id=keys)

    return len(auths)


async def get_user_sessions(user_id: int) -> list[UserAuthorization]:
    return list(
        await UserAuthorization.filter(user_id=user_id)
        .order_by("-active_at")
        .only("id", "platform", "device_model", "system_version", "app_version", "ip", "created_at", "active_at")
    )