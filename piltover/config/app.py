from os import environ
from typing import Literal

from pydantic import BaseModel, Base64Bytes, Field
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource, TomlConfigSettingsSource


class _DcAddress(BaseModel):
    host: str
    port: int


class _Dc(BaseModel):
    id: int
    addresses: list[_DcAddress]


class _Gifs(BaseModel):
    provider: Literal["klipy"]
    api_key: str


class _AuthRateLimit(BaseModel):
    enabled: bool = True
    send_code_min_interval_seconds: int = Field(default=60, ge=0)
    send_code_per_ip_limit: int = Field(default=5, ge=1)
    send_code_per_ip_window_seconds: int = Field(default=3600, ge=60)
    send_code_per_key_limit: int = Field(default=20, ge=1)
    send_code_per_key_window_seconds: int = Field(default=3600, ge=60)
    sign_in_fail_limit: int = Field(default=5, ge=1)
    sign_in_fail_window_seconds: int = Field(default=3600, ge=60)
    shadow_ban_fail_threshold: int = Field(default=15, ge=1)
    shadow_ban_duration_seconds: int = Field(default=86400, ge=60)


class _AppConfig(BaseModel):
    dc_list: list[_Dc]
    this_dc: int
    name: str = "Piltover"
    system_user_username: str = "piltover"

    basic_group_member_limit: int = Field(default=50, ge=3, le=200)
    super_group_member_limit: int = Field(default=1000, ge=3, le=100000)
    edit_time_limit: int = 48 * 60 * 60
    max_message_length: int = Field(default=4096, ge=0, le=4096)
    max_caption_length: int = Field(default=2048, ge=0, le=4096)
    channels_per_user_limit: int = 100
    public_channels_limit: int = 10
    pinned_dialogs_limit: int = 5
    faved_stickers_limit: int = 15
    saved_gifs_limit: int = 100
    recent_stickers_limit: int = 25
    reactions_unique_max: int = 11
    user_bio_limit: int = 100
    basic_group_admin_limit: int = 10
    channel_admin_limit: int = 25

    hmac_key: Base64Bytes
    file_ref_expire_minutes: int = 60 * 4
    contact_token_expire_seconds: int = 60 * 30
    srp_password_reset_wait_seconds: int = 86400 * 7
    scheduled_instant_send_threshold: int = 30
    account_delete_wait_seconds: int = 86400 * 7
    channel_delete_history_min_id_threshold: int = 1000
    max_bots_per_user: int = 24

    gifs: _Gifs | None = None
    auth_rate_limit: _AuthRateLimit = Field(default_factory=_AuthRateLimit)


class AppConfig(BaseSettings):
    app: _AppConfig = Field(init=False)

    model_config = SettingsConfigDict(toml_file=environ.get("APP_CONFIG", "config/app.toml"))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return TomlConfigSettingsSource(settings_cls),


APP_CONFIG = AppConfig().app

DICE_CONFIG = {
    "\U0001F3B2": (6, 62),  # Die
    "\U0001F3AF": (6, 62),  # Target
    "\U0001F3C0": (5, 110),  # Basketball
    "\u26bd": (5, 110),  # Football
    "\u26bd\ufe0f": (5, 110),  # Football
    "\U0001F3B0": (64, 110),  # Slot machine
    "\U0001F3B3": (6, 110),  # Bowling
}
