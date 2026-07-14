from __future__ import annotations

import asyncio
import hashlib
import hmac
from asyncio import Queue, Event
from collections import deque, OrderedDict
from copy import deepcopy
from time import time
from typing import cast, TYPE_CHECKING

from loguru import logger
from mtproto.transport.packets import DecryptedMessagePacket
from tortoise.expressions import F, Q

import piltover
from piltover.auth_data import AuthData
from piltover.cache import Cache
from piltover.db.enums import PrivacyRuleKeyType
from piltover.db.models import UserAuthorization, AuthKey, ChatParticipant, PollVote, Contact, PrivacyRule, Presence, \
    MessageRef
from piltover.exceptions import Unreachable
from piltover.tl import Updates, Long, Int, BadServerSalt, BadMsgNotification
from piltover.tl.core_types import TLObject, Message, MsgContainer
from piltover.tl.types.internal import ObjectWithLayerRequirement, TaggedLongVector, NeedsContextValues
from piltover.tl.utils import is_content_related, is_id_strictly_not_content_related, is_id_strictly_content_related
from piltover.utils.debug import measure_time
from piltover.tl.serialization_context import SerializationContext, ContextValues

if TYPE_CHECKING:
    from piltover.gateway import Client


class Salt:
    __slots__ = ("salt", "valid_at",)

    def __init__(self, salt: bytes, valid_at: int) -> None:
        self.salt = salt
        self.valid_at = valid_at


class MsgIdValues:
    __slots__ = ("last_time", "offset",)

    def __init__(self, last_time: int = 0, offset: int = 0) -> None:
        self.last_time = last_time
        self.offset = offset


_RPC_COMPLETION_TRACK_LIMIT = 256


class Session:
    __slots__ = (
        "client", "session_id", "auth_data", "min_msg_id", "user_id", "auth_id", "channel_ids", "auth_loaded_at",
        "channels_loaded_at", "salt_now", "salt_prev", "no_updates", "layer", "is_bot", "mfa_pending", "msg_id_values",
        "out_seq_no", "message_queue", "unencrypted_queue", "message_available", "is_internal_push",
        "had_init_connection", "pending_outbound", "resend_pending_on_connect", "_cleanup_task", "_enqueue_lock",
        "_rpc_completion_lock", "_rpc_completions", "_rpc_completed_ids", "_rpc_completed_order", "_upd_seq",
        "_flush_task", "_outbound_flush_lock",
    )

    def __init__(self, session_id: int, client: Client | None = None, auth_data: AuthData | None = None) -> None:
        self.client = client
        self.session_id = session_id
        self.auth_data = auth_data

        self.min_msg_id = 0
        self.msg_id_values = MsgIdValues()
        self.out_seq_no = 0

        self.user_id: int | None = None
        self.auth_id: int | None = None
        self.is_bot = False
        self.mfa_pending = False
        self.auth_loaded_at = 0.
        self.had_init_connection = False

        self.channel_ids: set[int] = set()
        self.channels_loaded_at = 0.

        self.salt_now = Salt(b"\x00" * 8, 0)
        self.salt_prev = Salt(b"\x00" * 8, 0)

        self.no_updates = False
        self.layer = 133
        self.is_internal_push = False

        self.message_queue = Queue()
        self.unencrypted_queue = Queue()
        self.message_available: Event | None = None
        self._enqueue_lock = asyncio.Lock()

        self._rpc_completion_lock = asyncio.Lock()
        self._rpc_completions: dict[int, Event] = {}
        self._rpc_completed_ids: set[int] = set()
        self._rpc_completed_order: deque[int] = deque()
        self._upd_seq: int | None = None
        self._flush_task: asyncio.Task | None = None
        self._outbound_flush_lock = asyncio.Lock()

        self.pending_outbound: OrderedDict[int, tuple[int, bytes]] = OrderedDict()
        self.resend_pending_on_connect = False
        self._cleanup_task: asyncio.Task | None = None

        # TODO: store whole session in redis or something

    def uniq_id(self) -> tuple[int, int]:
        key_id = 0 if self.auth_data is None or self.auth_data.auth_key_id is None else self.auth_data.auth_key_id
        return key_id, self.session_id

    def __hash__(self) -> int:
        return hash(self.uniq_id)

    def cancel_cleanup(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        self._cleanup_task = None

    def connect(self, client: Client) -> None:
        self.cancel_cleanup()
        self.client = client
        self.message_available = client.message_available
        if self.pending_outbound:
            self.resend_pending_on_connect = True
        if not self.message_queue.empty() or self.resend_pending_on_connect:
            self.message_available.set()
        piltover.session.SessionManager.broker.subscribe(self)
        self._schedule_outbound_flush()

    def disconnect(self) -> None:
        was_connected = self.client is not None
        self.client = None
        self.message_available = None
        self.had_init_connection = False
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None
        if was_connected:
            piltover.session.SessionManager.schedule_cleanup(self)

    def track_pending_outbound(self, message_id: int, seq_no: int, data: bytes) -> None:
        self.pending_outbound[message_id] = (seq_no, data)

    def ack_outbound(self, msg_ids: list[int]) -> None:
        for msg_id in msg_ids:
            self.pending_outbound.pop(msg_id, None)

    def finalize(self) -> None:
        self.cancel_cleanup()
        self.client = None
        self.message_available = None
        self._rpc_completions.clear()
        self._rpc_completed_ids.clear()
        self._rpc_completed_order.clear()
        self._upd_seq = None
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
        self._flush_task = None
        self.pending_outbound.clear()
        self.resend_pending_on_connect = False
        while not self.message_queue.empty():
            self.message_queue.get_nowait()
        while not self.unencrypted_queue.empty():
            self.unencrypted_queue.get_nowait()

    def _prune_rpc_completions(self) -> None:
        while len(self._rpc_completed_ids) > _RPC_COMPLETION_TRACK_LIMIT:
            oldest = self._rpc_completed_order.popleft()
            self._rpc_completed_ids.discard(oldest)
            self._rpc_completions.pop(oldest, None)

    async def mark_rpc_completed(self, msg_id: int) -> None:
        async with self._rpc_completion_lock:
            if msg_id not in self._rpc_completed_ids:
                self._rpc_completed_ids.add(msg_id)
                self._rpc_completed_order.append(msg_id)
                self._prune_rpc_completions()

            if event := self._rpc_completions.get(msg_id):
                event.set()

    async def wait_for_rpc(self, msg_id: int, timeout: float) -> bool:
        async with self._rpc_completion_lock:
            if msg_id in self._rpc_completed_ids:
                return True
            event = self._rpc_completions.get(msg_id)
            if event is None:
                event = Event()
                self._rpc_completions[msg_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout)
            return True
        except TimeoutError:
            return False

    @staticmethod
    def _get_attr_or_element(obj: TLObject | list, field_name: str) -> TLObject | list:
        if isinstance(obj, list):
            return obj[int(field_name)]
        else:
            return getattr(obj, field_name)

    @staticmethod
    async def _persist_upd_seq(auth_id: int, seq: int) -> None:
        await UserAuthorization.filter(id=auth_id).update(upd_seq=seq)

    async def _next_upd_seq(self) -> int:
        if self.auth_id is None:
            return 0
        if self._upd_seq is None:
            self._upd_seq = await UserAuthorization.filter(id=self.auth_id).values_list("upd_seq", flat=True)
            if self._upd_seq is None:
                self._upd_seq = 0
        self._upd_seq += 1
        seq = self._upd_seq
        asyncio.create_task(self._persist_upd_seq(self.auth_id, seq))
        return seq

    async def enqueue_unencrypted(self, obj: TLObject, in_reply: bool = True) -> None:
        async with self._enqueue_lock:
            message_id = self.msg_id(in_reply=in_reply)
            logger.debug(
                "Queueing unencrypted message {message_id} to {session_id}: {message!r}",
                message_id=message_id, session_id=self.session_id, message=obj,
            )
            self.unencrypted_queue.put_nowait((message_id, obj.write()))

        if self.message_available is not None:
            self.message_available.set()
        self._schedule_outbound_flush()

    def _prepare_outbound_obj(self, obj: TLObject) -> TLObject:
        obj = deepcopy(obj)
        if isinstance(obj, ObjectWithLayerRequirement):
            field_paths = obj.fields
            obj = obj.object

            for field_path in field_paths:
                if field_path.min_layer <= self.layer <= field_path.max_layer:
                    continue

                field_path = field_path.field.split(".")
                parent = obj
                for field_name in field_path[:-1]:
                    parent = self._get_attr_or_element(parent, field_name)

                if not isinstance(parent, list):
                    continue

                del parent[int(field_path[-1])]

        return obj

    async def flush_outbound(self) -> None:
        async with self._outbound_flush_lock:
            client = self.client
            if client is None:
                return
            await client._write_session_queues(self)

    async def _flush_outbound_once(self) -> None:
        await self.flush_outbound()
        if self.message_queue.qsize() > 0:
            self._schedule_outbound_flush()

    def _schedule_outbound_flush(self) -> None:
        if self.client is None:
            return
        if self._flush_task is not None and not self._flush_task.done():
            return
        self._flush_task = asyncio.create_task(self._flush_outbound_once())

    async def enqueue(self, obj: TLObject, in_reply: bool) -> None:
        async with self._enqueue_lock:
            obj = self._prepare_outbound_obj(obj)
            context_values = None
            if isinstance(obj, NeedsContextValues):
                with measure_time("._resolve_context_values(...)"):
                    context_values = await self._resolve_context_values(obj)
                obj = obj.obj

            if isinstance(obj, Updates) and self.auth_id is not None:
                obj.seq = await self._next_upd_seq()
                logger.trace(f"setting seq to {obj.seq} for user {self.user_id}, auth {self.auth_id}")

            with measure_time("session.pack_message(...)"):
                message = self.pack_message(obj, in_reply)

            logger.debug(
                "Queueing message {message_id} to {session_id}: {message!r}",
                message_id=message.message_id, session_id=self.session_id, message=message,
            )

            with measure_time("<serialize message>"):
                ctx = SerializationContext(
                    auth_id=self.auth_id,
                    user_id=self.user_id,
                    layer=self.layer,
                    values=context_values,
                )
                data = message.obj.write(ctx)

            self.message_queue.put_nowait((message.message_id, message.seq_no, data))

            if isinstance(obj, Updates):
                obj.seq = 0
                obj.qts = 0

        if self.message_available is not None:
            self.message_available.set()
        self._schedule_outbound_flush()

    @staticmethod
    def make_salt(salt_key: bytes, auth_key_id: int, timestamp: int) -> bytes:
        return hmac.new(salt_key, Long.write(auth_key_id) + Int.write(timestamp), hashlib.sha1).digest()[:8]

    # TODO: store salt_key in session?
    def update_salts_maybe(self, salt_key: bytes, force: bool = False) -> None:
        if self.auth_data is None or self.auth_data.auth_key_id is None:
            self.salt_now = Salt(b"\x00" * 8, 0)
            self.salt_prev = Salt(b"\x00" * 8, 0)
            return

        now = int(time() // (30 * 60))
        if self.salt_now.valid_at == now and not force:
            return

        self.salt_now.salt = self.make_salt(salt_key, self.auth_data.auth_key_id, now)
        self.salt_now.valid_at = now

        self.salt_prev.salt = self.make_salt(salt_key, self.auth_data.auth_key_id, now - 1)
        self.salt_prev.valid_at = now - 1

    async def fetch_layer(self) -> None:
        if self.auth_data is None or self.auth_data.perm_auth_key_id is None:
            return

        perm_key_layer = cast(
            int | None,
            await AuthKey.get_or_none(id=self.auth_data.perm_auth_key_id).values_list("layer", flat=True),
        )
        if perm_key_layer is not None:
            from piltover.tl.layer_info import layer as max_layer

            self.layer = min(perm_key_layer, max_layer)

    def _reset_auth(self) -> None:
        piltover.session.SessionManager.broker.unsubscribe_auth(self.auth_id, self)
        piltover.session.SessionManager.broker.unsubscribe_user(self.user_id, self)
        piltover.session.SessionManager.broker.channels_diff_update(self, self.channel_ids, [])

        self.user_id = None
        self.auth_id = None
        self.is_bot = False
        self.mfa_pending = False
        self._upd_seq = None
        self.channel_ids.clear()

    async def refresh_auth_maybe(self, force_refresh_auth: bool = False) -> None:
        if self.auth_data is None:
            return

        if force_refresh_auth and self.auth_data.auth_key_id is not None:
            self.auth_data = await AuthKey.get_auth_data(self.auth_data.auth_key_id)

        auth_key_id = self.auth_data.auth_key_id
        perm_auth_key_id = self.auth_data.perm_auth_key_id

        old_user_id = self.user_id
        old_auth_id = self.auth_id

        if auth_key_id is None or perm_auth_key_id is None:
            self._reset_auth()
            return

        # TODO: dont try to refetch auth every time if it is None?
        if (time() - self.auth_loaded_at) > 60 or force_refresh_auth or self.auth_id is None:
            logger.trace("Refreshing auth...")
            self.auth_loaded_at = time()

            auth = await UserAuthorization.get_or_none(
                key_id=perm_auth_key_id,
            ).select_related("user").annotate(is_bot=F("user__bot")).only(
                "id", "user_id", "mfa_pending", "is_bot", "upd_seq",
            )
            if auth is not None:
                self.user_id = auth.user_id
                self.auth_id = auth.id
                self.is_bot = auth.is_bot
                self.mfa_pending = auth.mfa_pending
                self._upd_seq = auth.upd_seq
            else:
                self._reset_auth()
                return

        if self.auth_id is not None and not self.mfa_pending and (time() - self.channels_loaded_at) > 60 * 5:
            logger.trace("Refreshing channels...")
            self.channels_loaded_at = time()

            channel_ids: TaggedLongVector
            if (channel_ids := await Cache.obj.get(f"channels:{self.user_id}")) is None:
                channel_ids = TaggedLongVector(
                    vec=cast(list[int], await ChatParticipant.filter(
                        channel_id__not_isnull=True, user_id=self.user_id, left=False,
                    ).values_list("channel_id", flat=True)),
                )
                await Cache.obj.set(f"channels:{self.user_id}", channel_ids, ttl=60 * 10)

            channel_ids: list[int] = channel_ids.vec
            old_channels = set(self.channel_ids)
            new_channels = set(channel_ids)
            channels_to_delete = old_channels - new_channels
            channels_to_add = new_channels - old_channels

            self.channel_ids = new_channels
            piltover.session.SessionManager.broker.channels_diff_update(self, channels_to_delete, channels_to_add)

        if old_user_id != self.user_id:
            if old_user_id:
                piltover.session.SessionManager.broker.unsubscribe_user(old_user_id, self)
            piltover.session.SessionManager.broker.subscribe_user(self.user_id, self)
        if old_auth_id != self.auth_id:
            if old_auth_id:
                piltover.session.SessionManager.broker.unsubscribe_auth(old_auth_id, self)
            piltover.session.SessionManager.broker.subscribe_auth(self.auth_id, self)

    # https://core.telegram.org/mtproto/description#message-identifier-msg-id
    def msg_id(self, in_reply: bool) -> int:
        # Client message identifiers are divisible by 4, server message
        # identifiers modulo 4 yield 1 if the message is a response to
        # a client message, and 3 otherwise.

        now = int(time())
        self.msg_id_values.offset = (self.msg_id_values.offset + 4) if now == self.msg_id_values.last_time else 0
        self.msg_id_values.last_time = now
        msg_id = (now * 2 ** 32) + self.msg_id_values.offset + (1 if in_reply else 3)

        assert msg_id % 4 in [1, 3], f"Invalid server msg_id: {msg_id}"
        return msg_id

    def get_outgoing_seq_no(self, obj: TLObject) -> int:
        ret = self.out_seq_no * 2
        if is_content_related(obj):
            self.out_seq_no += 1
            ret += 1
        return ret

    def pack_message(self, obj: TLObject, in_reply: bool) -> Message:
        return Message(
            message_id=self.msg_id(in_reply=in_reply),
            seq_no=self.get_outgoing_seq_no(obj),
            obj=obj,
        )

    def pack_container(self, objects: list[tuple[TLObject, bool]]) -> Message:
        container = MsgContainer(messages=[
            Message(
                message_id=self.msg_id(in_reply=in_reply),
                seq_no=self.get_outgoing_seq_no(obj),
                obj=obj,
            )
            for obj, in_reply in objects
        ])

        return self.pack_message(container, False)

    async def _resolve_context_values(self, values: NeedsContextValues) -> ContextValues:
        result = ContextValues()

        # TODO: cache fetched values

        if values.poll_answers:
            selected_answers = await PollVote.filter(
                answer__poll_id__in=values.poll_answers, user_id=self.user_id,
            ).values_list("answer__poll_id", "answer_id")
            for poll_id, answer_id in selected_answers:
                if poll_id not in result.poll_answers:
                    result.poll_answers[poll_id] = set()
                result.poll_answers[poll_id].add(answer_id)

        if values.chat_participants or values.channel_participants:
            participants_q = Q()
            if values.chat_participants:
                participants_q |= Q(chat_id__in=values.chat_participants)
            if values.channel_participants:
                participants_q |= Q(channel_id__in=values.channel_participants)

            participants = await ChatParticipant.filter(participants_q, user_id=self.user_id).only(
                "chat_id", "channel_id", "admin_rights", "banned_rights", "invited_at", "left",
            )
            for participant in participants:
                if participant.chat_id is not None:
                    result.chat_participants[participant.chat_id] = participant
                elif participant.channel_id is not None:
                    result.channel_participants[participant.channel_id] = participant
                else:
                    raise Unreachable

        if values.users:
            contact_ids = set()
            for contact in await Contact.filter(
                Q(owner_id=self.user_id, target_id__in=values.users)
                | Q(owner_id__in=values.users, target_id=self.user_id)
            ):
                result.contacts[(contact.owner_id, contact.target_id)] = contact
                if contact.owner_id != self.user_id:
                    contact_ids.add(contact.owner_id)

            # NOTE (for future me refactoring this): this overwrites existing rules in context variables btw
            result.privacyrules = await PrivacyRule.has_access_to_bulk(
                users=values.users,
                user=self.user_id,
                keys=[
                    PrivacyRuleKeyType.PHONE_NUMBER,
                    PrivacyRuleKeyType.PROFILE_PHOTO,
                    PrivacyRuleKeyType.STATUS_TIMESTAMP,
                ],
                contacts=contact_ids,
            )

        # TODO: store list of mentioned users inside *ToFormat message
        # TODO: cache media unread statuses
        if values.channel_messages:
            messages = await MessageRef.filter(id__in=values.channel_messages).select_related(
                "peer", "peer__channel", "content", "content__media", "content__media__file",
            )
            mentioned_media_unreads = await MessageRef.get_mentioned_media_unread_bulk(messages, self.user_id)
            reactionss = await MessageRef.to_tl_reactions_bulk(messages, self.user_id)
            for message, mmu, reactions in zip(messages, mentioned_media_unreads, reactionss):
                result.channel_messages[message.id] = (reactions, mmu[0], mmu[1])

        return result

    async def is_message_bad(self, packet: DecryptedMessagePacket, check_salt: bool) -> bool:
        # https://core.telegram.org/mtproto/service_messages_about_messages#notice-of-ignored-error-message

        error_code = 0
        inner_id = Int.read_bytes(packet.data[:4], False)

        if packet.message_id % 4 != 0:
            # 18: incorrect two lower order msg_id bits (the server expects client message msg_id to be divisible by 4)
            logger.debug(f"Client sent message id which is not divisible by 4")
            error_code = 18
        elif (packet.message_id >> 32) < (time() - 300):
            # 16: msg_id too low
            logger.debug(f"Client sent message id which is too low")
            error_code = 16
        elif (packet.message_id >> 32) > (time() + 30):
            # 17: msg_id too high
            logger.debug(f"Client sent message id which is too low")
            error_code = 17
        elif (packet.seq_no & 1) == 1 and is_id_strictly_not_content_related(inner_id):
            # 34: an even msg_seqno expected (irrelevant message), but odd received
            logger.debug(f"Client sent odd seq_no for content-related message ({hex(inner_id)[2:]})")
            error_code = 34
        elif (packet.seq_no & 1) == 0 and is_id_strictly_content_related(inner_id):
            # 35: odd msg_seqno expected (relevant message), but even received
            logger.debug(f"Client sent even seq_no for not content-related message ({hex(inner_id)[2:]})")
            error_code = 35

        # TODO: add validation for message_id duplication (code 19)
        # TODO: what's the difference between code 16 and code 20???
        # TODO: add validation for seq_no too low/high (code 32 and 33)

        if error_code:
            await self.enqueue(
                obj=BadMsgNotification(
                    bad_msg_id=packet.message_id,
                    bad_msg_seqno=packet.seq_no,
                    error_code=error_code,
                ),
                in_reply=True,
            )
            return True

        # 48: incorrect server salt (in this case, the bad_server_salt response is received with the correct salt,
        # and the message is to be re-sent with it)
        if check_salt and packet.salt not in (self.salt_now.salt, self.salt_prev.salt):
            logger.debug(
                f"Client sent bad salt ({int.from_bytes(packet.salt, 'little')}) "
                f"in message {packet.message_id}, sending correct salt"
            )
            await self.enqueue(
                obj=BadServerSalt(
                    bad_msg_id=packet.message_id,
                    bad_msg_seqno=packet.seq_no,
                    error_code=48,
                    new_server_salt=Long.read_bytes(self.salt_now.salt),
                ),
                in_reply=True,
            )
            return True

        return False
