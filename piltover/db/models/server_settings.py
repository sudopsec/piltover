from __future__ import annotations

from tortoise import Model, fields


class ServerSettings(Model):
    id: int = fields.IntField(primary_key=True, default=1)
    reports_enabled: bool = fields.BooleanField(default=True)
    bot_creation_enabled: bool = fields.BooleanField(default=True)
    group_creation_enabled: bool = fields.BooleanField(default=True)
    channel_creation_enabled: bool = fields.BooleanField(default=True)
    phone_calls_enabled: bool = fields.BooleanField(default=True)
    verifybot_enabled: bool = fields.BooleanField(default=True)
    stars_bot_enabled: bool = fields.BooleanField(default=True)