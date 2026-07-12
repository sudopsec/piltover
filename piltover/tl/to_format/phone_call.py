import hashlib

from piltover.config import SYSTEM_CONFIG
from piltover.exceptions import Unreachable
from piltover.tl import types, PhoneConnection, PhoneConnectionWebrtc
from piltover.tl.serialization_context import EMPTY_SERIALIZATION_CONTEXT, SerializationContext

_STUN_SERVERS = (
    ("stun.l.google.com", 19302),
    ("stun1.l.google.com", 19302),
)


class PhoneCallToFormat(types.PhoneCallToFormatInternal):
    def _connections(self) -> list[PhoneConnection | PhoneConnectionWebrtc]:
        if self.connections:
            return self.connections
        if self.start_date is None:
            return []

        peer_tag = hashlib.sha256(f"call:{self.id}".encode()).digest()[:16]
        public_ip = SYSTEM_CONFIG.group_call_sfu.public_ip
        return [
            *[
                PhoneConnectionWebrtc(
                    id=index,
                    ip=host,
                    ipv6="",
                    port=port,
                    username="",
                    password="",
                    stun=True,
                )
                for index, (host, port) in enumerate(_STUN_SERVERS, start=1)
            ],
            PhoneConnection(
                tcp=False,
                id=111,
                ip=public_ip,
                ipv6="::",
                port=22345,
                peer_tag=peer_tag,
            ),
        ]

    def _write(self, ctx: SerializationContext) -> bytes:
        connections = self._connections()
        common_kwargs = {
            "id": self.id,
            "access_hash": self.access_hash,
            "date": self.date,
            "admin_id": self.admin_id,
            "participant_id": self.participant_id,
            "protocol": self.protocol,
        }

        if ctx.user_id == self.admin_id:
            if self.participant_sess_id is None:
                call = types.PhoneCallWaiting(
                    **common_kwargs,
                )
            elif self.g_a is None:
                call = types.PhoneCallAccepted(
                    **common_kwargs,
                    g_b=self.g_b,
                )
            else:
                call = types.PhoneCall(
                    **common_kwargs,
                    p2p_allowed=True,
                    g_a_or_b=self.g_b,
                    key_fingerprint=self.key_fingerprint or 0,
                    connections=connections,
                    start_date=self.start_date or 0,
                )
        elif ctx.user_id == self.participant_id:
            if self.participant_sess_id is None:
                call = types.PhoneCallRequested(
                    **common_kwargs,
                    g_a_hash=self.g_a_hash,
                )
            elif self.participant_sess_id == ctx.auth_id:
                if self.g_a is None:
                    call = types.PhoneCallWaiting(
                        **common_kwargs,
                    )
                else:
                    call = types.PhoneCall(
                        **common_kwargs,
                        p2p_allowed=True,
                        g_a_or_b=self.g_a,
                        key_fingerprint=self.key_fingerprint or 0,
                        connections=connections,
                        start_date=self.start_date or 0,
                    )
            else:
                call = types.PhoneCallDiscarded(
                    id=self.id,
                )
        else:
            raise Unreachable

        return call.write(ctx)

    def write(self, ctx: SerializationContext = EMPTY_SERIALIZATION_CONTEXT) -> bytes:
        if ctx.dont_format:
            return super().write(ctx)
        return self._write(ctx)
