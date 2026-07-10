from __future__ import annotations

from time import time

from loguru import logger

from piltover.cache import Cache
from piltover.config import APP_CONFIG
from piltover.exceptions import ErrorRpc


def _cfg():
    return APP_CONFIG.auth_rate_limit


def _flood_wait(seconds: int) -> None:
    raise ErrorRpc(error_code=420, error_message=f"FLOOD_WAIT_{max(seconds, 1)}")


async def _bump_counter(key: str, window_seconds: int) -> int:
    if not await Cache.obj.exists(key):
        await Cache.obj.set(key, 1, ttl=window_seconds)
        return 1
    return await Cache.obj.increment(key, 1)


async def _seconds_until(key: str, min_interval: int) -> int | None:
    last_sent = await Cache.obj.get(key)
    if last_sent is None:
        return None
    elapsed = int(time()) - int(last_sent)
    if elapsed < min_interval:
        return min_interval - elapsed
    return None


async def check_send_code_allowed(ip: str, auth_key_id: int | None) -> None:
    cfg = _cfg()

    if (wait := await _seconds_until(f"auth:send:last:{ip}", cfg.send_code_min_interval_seconds)) is not None:
        _flood_wait(wait)

    ip_count = await Cache.obj.get(f"auth:send:ip:{ip}")
    if ip_count is not None and int(ip_count) >= cfg.send_code_per_ip_limit:
        _flood_wait(cfg.send_code_min_interval_seconds)

    if auth_key_id is not None:
        key_count = await Cache.obj.get(f"auth:send:key:{auth_key_id}")
        if key_count is not None and int(key_count) >= cfg.send_code_per_key_limit:
            _flood_wait(cfg.send_code_min_interval_seconds)


async def record_send_code(ip: str, auth_key_id: int | None) -> None:
    cfg = _cfg()
    now = int(time())

    await Cache.obj.set(
        f"auth:send:last:{ip}",
        now,
        ttl=cfg.send_code_per_ip_window_seconds,
    )
    await _bump_counter(f"auth:send:ip:{ip}", cfg.send_code_per_ip_window_seconds)
    if auth_key_id is not None:
        await _bump_counter(f"auth:send:key:{auth_key_id}", cfg.send_code_per_key_window_seconds)


async def is_shadow_banned(ip: str) -> bool:
    return await Cache.obj.exists(f"auth:shadow:{ip}")


async def check_sign_in_allowed(ip: str, auth_key_id: int | None) -> None:
    if await is_shadow_banned(ip):
        raise ErrorRpc(error_code=400, error_message="PHONE_CODE_INVALID")

    cfg = _cfg()
    failures = 0
    ip_failures = await Cache.obj.get(f"auth:fail:ip:{ip}")
    if ip_failures is not None:
        failures = max(failures, int(ip_failures))
    if auth_key_id is not None:
        key_failures = await Cache.obj.get(f"auth:fail:key:{auth_key_id}")
        if key_failures is not None:
            failures = max(failures, int(key_failures))

    if failures >= cfg.sign_in_fail_limit:
        _flood_wait(min(600, 60 * (failures - cfg.sign_in_fail_limit + 1)))


async def record_sign_in_failure(ip: str, auth_key_id: int | None) -> None:
    cfg = _cfg()

    ip_failures = await _bump_counter(f"auth:fail:ip:{ip}", cfg.sign_in_fail_window_seconds)
    if auth_key_id is not None:
        key_failures = await _bump_counter(f"auth:fail:key:{auth_key_id}", cfg.sign_in_fail_window_seconds)
    else:
        key_failures = 0

    failures = max(ip_failures, key_failures)
    if failures >= cfg.shadow_ban_fail_threshold:
        await Cache.obj.set(f"auth:shadow:{ip}", 1, ttl=cfg.shadow_ban_duration_seconds)
        logger.warning("Shadow ban applied for ip {ip} after {failures} failed sign-in attempts", ip=ip, failures=failures)


async def clear_sign_in_failures(ip: str, auth_key_id: int | None) -> None:
    await Cache.obj.delete(f"auth:fail:ip:{ip}")
    if auth_key_id is not None:
        await Cache.obj.delete(f"auth:fail:key:{auth_key_id}")