import pytest

from piltover.app.handlers.help import APP_CONFIG_JSON, get_app_config, get_premium_promo
from piltover.tl import JsonBool, JsonObjectValue
from piltover.tl.functions.help import GetAppConfig


def _app_config_bool(key: str) -> bool:
    for entry in APP_CONFIG_JSON.value:
        if isinstance(entry, JsonObjectValue) and entry.key == key:
            assert isinstance(entry.value, JsonBool)
            return entry.value.value
    raise KeyError(key)


@pytest.mark.asyncio
async def test_app_config_enables_premium_and_stars() -> None:
    assert _app_config_bool("premium_purchase_blocked") is False
    assert _app_config_bool("stars_purchase_blocked") is False
    assert _app_config_bool("stargifts_blocked") is False
    assert _app_config_bool("stars_gifts_enabled") is True

    result = await get_app_config(GetAppConfig(hash=0))
    assert result.config is APP_CONFIG_JSON


@pytest.mark.asyncio
async def test_premium_promo_uses_premiumbot() -> None:
    promo = await get_premium_promo()
    assert promo.period_options
    assert promo.period_options[0].bot_url == "t.me/premiumbot"