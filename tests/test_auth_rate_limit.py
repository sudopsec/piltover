import pytest

from piltover.cache import Cache
from piltover.config import APP_CONFIG
from piltover.exceptions import ErrorRpc
from piltover.app.utils import auth_rate_limit as rl


@pytest.fixture(autouse=True)
def memory_cache() -> None:
    Cache.init("memory")


@pytest.fixture(autouse=True)
def strict_rate_limits() -> None:
    APP_CONFIG.auth_rate_limit.send_code_min_interval_seconds = 60
    APP_CONFIG.auth_rate_limit.send_code_per_ip_limit = 2
    APP_CONFIG.auth_rate_limit.send_code_per_ip_window_seconds = 3600
    APP_CONFIG.auth_rate_limit.send_code_per_key_limit = 3
    APP_CONFIG.auth_rate_limit.send_code_per_key_window_seconds = 3600
    APP_CONFIG.auth_rate_limit.sign_in_fail_limit = 3
    APP_CONFIG.auth_rate_limit.sign_in_fail_window_seconds = 3600
    APP_CONFIG.auth_rate_limit.shadow_ban_fail_threshold = 5
    APP_CONFIG.auth_rate_limit.shadow_ban_duration_seconds = 3600


@pytest.mark.asyncio
async def test_send_code_min_interval() -> None:
    await rl.record_send_code("10.0.0.1", 1)
    with pytest.raises(ErrorRpc) as exc:
        await rl.check_send_code_allowed("10.0.0.1", 1)
    assert exc.value.error_code == 420
    assert exc.value.error_message.startswith("FLOOD_WAIT_")


@pytest.mark.asyncio
async def test_send_code_ip_limit() -> None:
    APP_CONFIG.auth_rate_limit.send_code_min_interval_seconds = 0
    await rl.record_send_code("10.0.0.1", 1)
    await rl.record_send_code("10.0.0.1", 2)
    with pytest.raises(ErrorRpc) as exc:
        await rl.check_send_code_allowed("10.0.0.1", 3)
    assert exc.value.error_code == 420


@pytest.mark.asyncio
async def test_sign_in_flood_after_failures() -> None:
    for _ in range(APP_CONFIG.auth_rate_limit.sign_in_fail_limit):
        await rl.record_sign_in_failure("10.0.0.1", 1)
    with pytest.raises(ErrorRpc) as exc:
        await rl.check_sign_in_allowed("10.0.0.1", 1)
    assert exc.value.error_code == 420


@pytest.mark.asyncio
async def test_shadow_ban_hides_behind_invalid_code() -> None:
    for _ in range(APP_CONFIG.auth_rate_limit.shadow_ban_fail_threshold):
        await rl.record_sign_in_failure("10.0.0.1", 1)
    assert await rl.is_shadow_banned("10.0.0.1")
    with pytest.raises(ErrorRpc) as exc:
        await rl.check_sign_in_allowed("10.0.0.1", 1)
    assert exc.value.error_code == 400
    assert exc.value.error_message == "PHONE_CODE_INVALID"


@pytest.mark.asyncio
async def test_clear_sign_in_failures() -> None:
    await rl.record_sign_in_failure("10.0.0.1", 1)
    await rl.clear_sign_in_failures("10.0.0.1", 1)
    await rl.check_sign_in_allowed("10.0.0.1", 1)