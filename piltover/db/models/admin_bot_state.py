from __future__ import annotations

from tortoise import fields

from piltover.db.enums import AdminBotState
from piltover.db.models.bot_state_base import BotUserStateBase


class AdminBotUserState(BotUserStateBase):
    state: AdminBotState = fields.IntEnumField(AdminBotState, description="")