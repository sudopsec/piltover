from io import BytesIO

from piltover.context import NeedContextValuesContext
from piltover.tl import types
from piltover.tl.serialization_context import EMPTY_SERIALIZATION_CONTEXT, SerializationContext


class ChannelToFormat(types.ChannelToFormatInternal):
    __slots__ = ("forum", "call_active", "call_not_empty")

    def __init__(self, *, forum: bool = False, call_active: bool = False, call_not_empty: bool = False, **kwargs):
        super().__init__(**kwargs)
        self.forum = forum
        self.call_active = call_active
        self.call_not_empty = call_not_empty

    def _forbidden(self, access_hash: int = -1) -> types.ChannelForbidden:
        from piltover.db.models import Channel

        return types.ChannelForbidden(
            id=Channel.make_id_from(self.id),
            access_hash=access_hash,
            title=self.title,
            broadcast=self.broadcast,
            megagroup=self.megagroup,
        )

    def _write(self, ctx: SerializationContext) -> bytes:
        from piltover.db.models import Channel
        from piltover.db.models.channel import CREATOR_RIGHTS
        from piltover.db.enums import ChatAdminRights, ChatBannedRights

        if ctx.values is None:
            return self._forbidden(0).write(ctx)

        participant = ctx.values.channel_participants.get(self.id) if ctx.values is not None else None

        is_creator = ctx.user_id is not None and self.creator_id == ctx.user_id

        if participant is not None and participant.banned_rights & ChatBannedRights.VIEW_MESSAGES:
            return self._forbidden(-1).write(ctx)

        if participant is None and not (self.nojoin_allow_view or self.username is not None or is_creator):
            return self._forbidden(-1).write(ctx)

        admin_rights = None
        if is_creator:
            admin_rights = CREATOR_RIGHTS
            if participant is not None and bool(participant.admin_rights & ChatAdminRights.ANONYMOUS):
                admin_rights = types.ChatAdminRights.read(BytesIO(admin_rights.write()))
                admin_rights.anonymous = True
        elif participant is not None and participant.is_admin:
            admin_rights = participant.admin_rights.to_tl()

        date = self.created_at
        if participant is not None and not participant.left:
            date = int(participant.invited_at.timestamp())

        return types.Channel(
            id=Channel.make_id_from(self.id),
            title=self.title,
            photo=self.photo if self.photo else types.ChatPhotoEmpty(),
            date=date,
            creator=is_creator,
            left=False if is_creator else (participant is None or participant.left),
            broadcast=self.broadcast,
            megagroup=self.megagroup,
            signatures=self.signatures,
            has_link=self.has_link,
            slowmode_enabled=self.slowmode_enabled,
            noforwards=self.noforwards,
            join_to_send=self.join_to_send,
            join_request=self.join_request,
            forum=self.forum,
            call_active=self.call_active,
            call_not_empty=self.call_not_empty,
            stories_hidden=False,
            stories_hidden_min=True,
            stories_unavailable=True,
            access_hash=-1,
            restriction_reason=None,
            admin_rights=admin_rights,
            username=self.username,
            usernames=[],
            default_banned_rights=self.default_banned_rights,
            banned_rights=participant.banned_rights.to_tl() if participant is not None else None,
            color=self.color,
            profile_color=self.profile_color,
            verified=self.verified,
        ).write(ctx)

    def write(self, ctx: SerializationContext = EMPTY_SERIALIZATION_CONTEXT) -> bytes:
        if ctx.dont_format:
            return super().write(ctx)
        return self._write(ctx)

    def check_for_ctx_values(self, values: NeedContextValuesContext) -> None:
        values.channel_participants.add(self.id)
