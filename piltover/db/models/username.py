from __future__ import annotations

from tortoise import fields, Model
from tortoise.fields import OneToOneNullableRelation

from piltover.app.utils.utils import is_username_valid, normalize_username
from piltover.db import models
from piltover.exceptions import ErrorRpc


def NullableOneToOne(to: str, related_name: str) -> OneToOneNullableRelation:
    return fields.OneToOneField(to, null=True, default=None, related_name=related_name)


class Username(Model):
    id: int = fields.BigIntField(primary_key=True)
    username: str = fields.CharField(max_length=32, unique=True)
    user: models.User | None = NullableOneToOne("models.User", related_name="username")
    channel: models.Channel | None = NullableOneToOne("models.Channel", related_name="username")

    user_id: int | None
    channel_id: int | None

    async def save(self, *args, **kwargs) -> None:
        self.username = normalize_username(self.username)
        if not is_username_valid(self.username):
            raise ErrorRpc(error_code=400, error_message="USERNAME_INVALID")
        await super().save(*args, **kwargs)