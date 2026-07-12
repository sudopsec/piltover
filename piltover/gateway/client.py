from __future__ import annotations

import asyncio
import struct
import time
from asyncio import Event
from io import BytesIO
from typing import TYPE_CHECKING, cast, Any, Literal

from loguru import logger
from lru import LRU
from mtproto import ConnectionRole
from mtproto.enums import TransportEvent
from mtproto.transport import Connection
from mtproto.transport.packets import MessagePacket, EncryptedMessagePacket, UnencryptedMessagePacket, \
    DecryptedMessagePacket, ErrorPacket, QuickAckPacket, BasePacket
from taskiq import AsyncTaskiqTask, TaskiqResult, TaskiqResultTimeoutError
from taskiq.brokers.inmemory_broker import InmemoryResultBackend
from taskiq.kicker import AsyncKicker

from piltover.auth_data import AuthData, GenAuthData
from piltover.exceptions import Disconnection, InvalidConstructorException, Unreachable
from piltover.gateway._keygen_handlers import KEYGEN_HANDLERS
from piltover.gateway._system_handlers import SYSTEM_HANDLERS
from piltover.session import Session, SessionManager
from piltover.tl import NewSessionCreated, Long, Int, RpcError, ReqPq, ReqPqMulti, MsgsAck
from piltover.tl.core_types import TLObject, MsgContainer, Message, RpcResult
from piltover.tl.functions.auth import BindTempAuthKey
from piltover.tl.functions.internal import CallRpc
from piltover.tl.types.internal import RpcResponse
from piltover.utils.debug import measure_time
from ..db.models import AuthKey

if TYPE_CHECKING:
    from .server import Gateway


_check_req_pq_tlid = (
    Int.write(ReqPq.tlid(), False),
    Int.write(ReqPqMulti.tlid(), False),
)

_SLOW_RPC_METHODS = frozenset({
    "SendMedia", "SendMedia_148", "SendMedia_176",
    "SendMultiMedia", "SendMultiMedia_148", "SendMultiMedia_176",
    "UploadMedia", "UploadMedia_133",
    "UploadProfilePhoto",
    "UploadWallPaper", "UploadWallPaper_133",
    "SaveFilePart", "SaveBigFilePart",
})


def _task_result_timeouts(method_name: str) -> tuple[float, float]:
    from piltover.config import GATEWAY_CONFIG

    ack_timeout = 1.5
    result_timeout = 30.0
    slow_timeout = 600.0
    if GATEWAY_CONFIG is not None:
        ack_timeout = GATEWAY_CONFIG.task_ack_timeout
        result_timeout = GATEWAY_CONFIG.task_result_timeout
        slow_timeout = GATEWAY_CONFIG.task_result_slow_timeout

    if method_name in _SLOW_RPC_METHODS:
        return ack_timeout, slow_timeout
    return ack_timeout, result_timeout


AuthGateResult = Literal["ok", "drop", "unregistered"]


class Client:
    __slots__ = (
        "server", "reader", "writer", "conn", "peername", "gen_auth_data", "keygen_session", "disconnect_timeout",
        "write_lock", "active_sessions", "active_keys", "message_available", "loop", "tasks", "_rejected_auth_keys",
    )

    def __init__(self, server: Gateway, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server = server

        self.reader = reader
        self.writer = writer
        self.conn = Connection(role=ConnectionRole.SERVER)
        self.peername: tuple[str, int] = writer.get_extra_info("peername")

        self.gen_auth_data: GenAuthData | None = None

        self.disconnect_timeout: asyncio.Timeout | None = None
        self.write_lock = asyncio.Lock()

        self.active_sessions = LRU(4, callback=self._session_evicted)
        self.active_keys = cast("LRU[int, bytes]", LRU(8))

        self.message_available = Event()
        self.keygen_session = Session(0)
        self.keygen_session.client = self
        self.keygen_session.message_available = self.message_available
        self.loop = asyncio.get_running_loop()
        self.tasks = set()
        self._rejected_auth_keys: set[int] = set()

    @staticmethod
    def _session_evicted(_: Any, session: Session) -> None:
        session.disconnect()

    def _get_cached_session(self, auth_key_id: int, session_id: int) -> Session | None:
        uniq_id = (auth_key_id, session_id)
        if uniq_id in self.active_sessions:
            return self.active_sessions[uniq_id]

    def _get_session(self, session_id: int, auth_data: AuthData) -> tuple[Session, bool]:
        if (cached := self._get_cached_session(auth_data.auth_key_id, session_id)) is not None:
            return cached, False

        session, created = SessionManager.get_or_create(session_id, self, auth_data)
        session.connect(self)

        self.active_sessions[session.uniq_id()] = session
        return session, created

    def _drop_auth_key(self, auth_key_id: int, session: Session) -> None:
        try:
            del self.active_keys[auth_key_id]
        except KeyError:
            pass
        uniq_id = session.uniq_id()
        try:
            del self.active_sessions[uniq_id]
        except KeyError:
            pass
        session.disconnect()

    async def _auth_gate(self, auth_key_id: int, obj: TLObject | None = None) -> AuthGateResult:
        if auth_key_id in self._rejected_auth_keys:
            return "drop"
        if not await AuthKey.is_registered(auth_key_id):
            return "unregistered"
        if isinstance(obj, BindTempAuthKey):
            if not await AuthKey.can_bind_temp_auth_key(auth_key_id, obj.perm_auth_key_id):
                return "unregistered"
        return "ok"

    @staticmethod
    def _auth_key_unregistered_result(message_id: int) -> RpcResult:
        return RpcResult(
            req_msg_id=message_id,
            result=RpcError(error_code=401, error_message="AUTH_KEY_UNREGISTERED"),
        )

    async def _respond_auth_key_unregistered(self, message_id: int, session: Session, auth_key_id: int) -> None:
        if auth_key_id in self._rejected_auth_keys:
            return

        self._rejected_auth_keys.add(auth_key_id)
        logger.info(
            "Auth key {auth_key_id} is not registered, sending AUTH_KEY_UNREGISTERED to {peer}",
            auth_key_id=auth_key_id,
            peer=self.peername,
        )
        await session.enqueue(self._auth_key_unregistered_result(message_id), True)
        self._drop_auth_key(auth_key_id, session)

    async def read_packet(self) -> MessagePacket | None:
        packet = self.conn.next_event()
        if packet is TransportEvent.DISCONNECT:
            raise Disconnection
        if isinstance(packet, MessagePacket):
            return packet

        try:
            recv = await self.reader.read(32 * 1024)
        except ConnectionResetError:
            raise Disconnection
        if not recv:
            raise Disconnection

        self.conn.data_received(recv)
        packet = self.conn.next_event()
        if packet is TransportEvent.DISCONNECT:
            raise Disconnection
        if not isinstance(packet, MessagePacket):
            return None

        return packet

    async def _write_packet(self, packet: BasePacket, ignore_errors: bool = False) -> None:
        to_send = self.conn.send(packet)
        try:
            async with self.write_lock:
                self.writer.write(to_send)
                await self.writer.drain()
        except ConnectionResetError:
            if ignore_errors:
                return
            raise Disconnection
        except Exception as e:
            if ignore_errors:
                return
            raise Disconnection from e

    async def _write_message(
            self, message_id: int, seq_no: int, data: bytes, session: Session,
    ) -> None:
        if not session.auth_data or session.auth_data.auth_key is None:
            raise Unreachable("Trying to send encrypted response, but auth_key is empty")

        logger.debug(f"Sending message {message_id} to {session.session_id}")

        session.update_salts_maybe(self.server.salt_key)

        decrypted = DecryptedMessagePacket(
            salt=session.salt_now.salt,
            session_id=session.session_id,
            message_id=message_id,
            seq_no=seq_no,
            data=data,
        )

        encrypted = decrypted.encrypt(session.auth_data.auth_key, ConnectionRole.SERVER)

        await self._write_packet(encrypted)

    async def _write_unencrypted(self, message_id: int, data: bytes) -> None:
        logger.debug("Sending unencrypted message {message_id}", message_id=message_id)
        await self._write_packet(UnencryptedMessagePacket(message_id, data))

    async def send_unencrypted(self, obj: TLObject) -> None:
        await self.keygen_session.enqueue_unencrypted(obj)

    async def _kiq(self, obj: TLObject, session: Session, message_id: int | None = None) -> AsyncTaskiqTask:
        # TODO: dont do .write.hex(), RpcResponse somehow doesn't need encoding it manually, check how exactly
        call_rpc = CallRpc(
            obj=obj,
            layer=session.layer,
            auth_key_id=session.auth_data.auth_key_id,
            perm_auth_key_id=session.auth_data.perm_auth_key_id,
            session_id=session.session_id,
            message_id=message_id,
            auth_id=session.auth_id,
            user_id=session.user_id,
            is_bot=session.is_bot,
            mfa_pending=session.mfa_pending,
            ip=self.peername[0],
        ).write().hex()

        with measure_time(".kiq()"):
            return await AsyncKicker(task_name=f"handle_tl_rpc", broker=self.server.broker, labels={}).kiq(call_rpc)

    async def handle_unencrypted_message(self, obj: TLObject) -> None:
        # TODO: move it to worker (and add db models to save auth key generation state)
        if obj.tlid() not in KEYGEN_HANDLERS:
            return

        try:
            await KEYGEN_HANDLERS[obj.tlid()](self, obj)
        except Disconnection as d:
            logger.opt(exception=d).warning(f"Requested disconnection while processing {obj.tlname()}")
            raise
        except Exception as e:
            logger.opt(exception=e).warning(f"Error while processing {obj.tlname()}")

    async def handle_encrypted_message(self, req_message: Message, session: Session) -> None:
        with measure_time("session.refresh_auth_maybe()"):
            await session.refresh_auth_maybe()

        if isinstance(req_message.obj, MsgContainer):
            logger.info(
                "gateway MsgContainer user={user_id} session={session_id} count={count} messages={messages}",
                user_id=session.user_id,
                session_id=session.session_id,
                count=len(req_message.obj.messages),
                messages=[
                    f"{msg.obj.tlname()}#{msg.message_id} {msg.obj!r}"
                    for msg in req_message.obj.messages
                ],
            )
            for msg in req_message.obj.messages:
                await self.propagate(msg, session)
        else:
            logger.info(
                "gateway rpc {method} user={user_id} session={session_id} msg_id={message_id} request={request!r}",
                method=req_message.obj.tlname(),
                user_id=session.user_id,
                session_id=session.session_id,
                message_id=req_message.message_id,
                request=req_message.obj,
            )
            await self.propagate(req_message, session)

    async def recv(self) -> None:
        packet = await self.read_packet()
        if packet is None:
            return

        await self._recv_packet(packet)

    async def _recv_packet(self, packet: MessagePacket) -> None:
        if isinstance(packet, EncryptedMessagePacket):
            if packet.auth_key_id in self._rejected_auth_keys:
                return

            auth_data = None
            if packet.auth_key_id in self.active_keys:
                auth_key = self.active_keys[packet.auth_key_id]
            else:
                auth_data = await self._get_auth_data(packet.auth_key_id)
                self.active_keys[packet.auth_key_id] = auth_key = cast(bytes, auth_data.auth_key)

            decrypted = await self.decrypt(packet, auth_key)

            session = self._get_cached_session(packet.auth_key_id, decrypted.session_id)
            if session is None:
                if auth_data is None:
                    auth_data = await self._get_auth_data(packet.auth_key_id)
                session, _ = self._get_session(decrypted.session_id, auth_data)
                session.update_salts_maybe(self.server.salt_key)

            if packet.needs_quick_ack:
                await self._write_packet(self._create_quick_ack(decrypted, session))

            # For some reason some clients cant process BadServerSalt response to BindTempAuthKey request
            check_salt = decrypted.data[:4] != Int.write(BindTempAuthKey.tlid(), False)
            if await session.is_message_bad(decrypted, check_salt):
                return

            try:
                message = Message(
                    message_id=decrypted.message_id,
                    seq_no=decrypted.seq_no,
                    obj=TLObject.read(BytesIO(decrypted.data)),
                )
            except (struct.error, ValueError, InvalidConstructorException) as e:
                logger.opt(exception=e).error("Failed to read object. Raw data: {raw_data}", raw_data=decrypted.data)
                constructor = e.constructor if isinstance(e, InvalidConstructorException) else 0
                await session.enqueue(
                    RpcResult(
                        req_msg_id=decrypted.message_id,
                        result=RpcError(
                            error_code=400,
                            error_message=f"INPUT_METHOD_INVALID_{constructor}_0",
                        ),
                    ),
                    False,
                )
                return

            if not session.min_msg_id:
                session.min_msg_id = message.message_id
                await session.fetch_layer()
                logger.info(f"({self.peername}) Created session {session.session_id}")
                await session.enqueue(
                    obj=NewSessionCreated(
                        first_msg_id=message.message_id,
                        unique_id=session.session_id,
                        server_salt=Long.read_bytes(session.salt_now.salt),
                    ),
                    in_reply=False,
                )

            logger.debug(
                "Received from {session_id} ({auth_key_id} {user_id}): {message}",
                session_id=session.session_id,
                auth_key_id=session.auth_data.auth_key_id,
                user_id=session.user_id,
                message=message,
            )

            match await self._auth_gate(packet.auth_key_id, message.obj):
                case "drop":
                    return
                case "unregistered":
                    await self._respond_auth_key_unregistered(message.message_id, session, packet.auth_key_id)
                    raise Disconnection(404)
                case "ok":
                    pass

            await self.handle_encrypted_message(message, session)
        elif isinstance(packet, UnencryptedMessagePacket):
            decoded = TLObject.read(BytesIO(packet.message_data))
            if isinstance(decoded, (ReqPq, ReqPqMulti)):
                peeked = self.conn.peek_packet()
                if peeked is TransportEvent.DISCONNECT:
                    raise Disconnection
                packet: UnencryptedMessagePacket | None = None
                while isinstance(peeked, UnencryptedMessagePacket) and peeked.message_data[:4] in _check_req_pq_tlid:
                    logger.debug("Skipping reqPQ: {req_pq}", req_pq=decoded)
                    packet = cast(UnencryptedMessagePacket, self.conn.next_event())
                    peeked = self.conn.peek_packet()
                    if peeked is TransportEvent.DISCONNECT:
                        raise Disconnection
                    await asyncio.sleep(0)

                if packet is not None:
                    decoded = TLObject.read(BytesIO(packet.message_data))

            logger.debug("{decoded}", decoded=decoded)
            await self.handle_unencrypted_message(decoded)

    async def _get_auth_data(self, auth_key_id: int) -> AuthData:
        logger.debug("Requested auth key: {auth_key_id}", auth_key_id=auth_key_id)
        if auth_key_id in self.server._unknown_auth_key_ids:
            raise Disconnection(404)

        data = await AuthKey.get_auth_data(auth_key_id, allow_expired=True)
        if data is None:
            self.server._unknown_auth_key_ids[auth_key_id] = None
            logger.info(
                "Client ({peer}) sent unknown auth_key_id {auth_key_id}, disconnecting with -404",
                peer=self.peername,
                auth_key_id=auth_key_id,
            )
            raise Disconnection(404)

        return data

    @staticmethod
    def _create_quick_ack(message: DecryptedMessagePacket, session: Session) -> QuickAckPacket:
        return message.quick_ack_response(session.auth_data.auth_key, ConnectionRole.CLIENT)

    @staticmethod
    async def decrypt(message: EncryptedMessagePacket, auth_key: bytes, v1: bool = False) -> DecryptedMessagePacket:
        try:
            return message.decrypt(auth_key, ConnectionRole.CLIENT, v1)
        except ValueError:
            logger.info("Failed to decrypt encrypted packet, disconnecting with 404")
            raise Disconnection(404)

    async def _worker_loop_recv(self) -> None:
        while True:
            try:
                while True:
                    packet = await self.read_packet()
                    if packet is None:
                        break
                    await self._recv_packet(packet)
            except Disconnection:
                raise
            except Exception as e:
                logger.opt(exception=e).error("An error occurred in recv loop")
                raise

    def _outbound_sessions(self) -> list[Session]:
        return [self.keygen_session, *list(self.active_sessions.values())]

    def _has_pending_outbound(self) -> bool:
        for session in self._outbound_sessions():
            if session.unencrypted_queue.qsize() or session.message_queue.qsize():
                return True
        return False

    async def _flush_session_outbound(self, session: Session) -> None:
        await session.flush_outbound()

    async def _write_session_queues(self, session: Session) -> None:
        while True:
            try:
                message_id, data = session.unencrypted_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._write_unencrypted(message_id, data)

        while True:
            try:
                message_id, seq_no, data = session.message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await self._write_message(message_id, seq_no, data, session)

    async def _worker_loop_send(self) -> None:
        while True:
            while self._has_pending_outbound():
                for session in self._outbound_sessions():
                    await self._flush_session_outbound(session)
            self.message_available.clear()
            if self._has_pending_outbound():
                continue
            await self.message_available.wait()

    async def _timer_task(self) -> None:
        try:
            async with self.disconnect_timeout:
                await asyncio.get_running_loop().create_future()
        except TimeoutError:
            ...

    @logger.catch
    async def worker(self):
        logger.debug("Client connected: {addr}", addr=self.peername)

        loop = asyncio.get_running_loop()
        self.disconnect_timeout = asyncio.timeout(None)

        done, pending = await asyncio.wait(
            [
                loop.create_task(self._timer_task()),
                loop.create_task(self._worker_loop_recv()),
                loop.create_task(self._worker_loop_send()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        try:
            for task in pending:
                task.cancel()

            for task in done:
                await task
        except Disconnection as err:
            if err.transport_error is not None:
                await self._write_packet(ErrorPacket(err.transport_error), ignore_errors=True)
        except TimeoutError:
            logger.debug("Client disconnected because of expired timeout")
        finally:
            logger.info("Client disconnected")

            try:
                self.writer.close()
                await self.writer.wait_closed()
            except ConnectionResetError:
                pass

            for session in self.active_sessions.values():
                logger.info(f"Session {session.session_id} removed")
                session.disconnect()

            self.active_sessions.clear()

    async def _wait_result_with_ack(
            self, task: AsyncTaskiqTask[str], message_id: int, session: Session, method_name: str,
    ) -> TaskiqResult[str]:
        start_time = time.perf_counter()
        result = None
        ack_timeout, result_timeout = _task_result_timeouts(method_name)

        try:
            result = await task.wait_result(timeout=ack_timeout)
            return result
        except TaskiqResultTimeoutError:
            logger.warning(
                "Task {method} ({message_id}) still running after {ack_timeout}s, sending MsgsAck "
                "(will wait up to {result_timeout}s more)",
                method=method_name,
                message_id=message_id,
                ack_timeout=ack_timeout,
                result_timeout=result_timeout,
            )
            await session.enqueue(MsgsAck(msg_ids=[message_id]), False)
            result = await task.wait_result(timeout=result_timeout)
            return result
        finally:
            end_time = time.perf_counter()
            logger.debug(
                "\"{method_name}\" ({message_id}) took {time_taken:.2f} ms to execute (taskiq reported {taskiq_time}s)",
                method_name=method_name,
                message_id=message_id,
                time_taken=(end_time - start_time) * 1000,
                taskiq_time=result.execution_time if result else None,
            )

    async def _process_request(self, request: Message, session: Session) -> RpcResult | None:
        if request.obj.tlid() in SYSTEM_HANDLERS:
            return await SYSTEM_HANDLERS[request.obj.tlid()](self, request, session)

        auth_key_id = cast(int, session.auth_data.auth_key_id)
        match await self._auth_gate(auth_key_id, request.obj):
            case "drop":
                return None
            case "unregistered":
                await self._respond_auth_key_unregistered(request.message_id, session, auth_key_id)
                raise Disconnection(404)
            case "ok":
                pass

        with measure_time("\"execute task\""):
            with measure_time("_kiq()"):
                task = await self._kiq(request.obj, session, request.message_id)
            with measure_time(".wait_result()"):
                try:
                    task_result = await self._wait_result_with_ack(
                        task, request.message_id, session, request.obj.__class__.__name__
                    )
                except Exception as e:
                    logger.opt(exception=e).error(f"Failed to get result for request {request!r}")
                    return RpcResult(
                        req_msg_id=request.message_id,
                        result=RpcError(error_code=500, error_message="INTERNAL_SERVER_ERROR_TIMEOUT"),
                    )

        if task_result.is_err:
            logger.opt(exception=task_result.error).error("An error occurred in worker while processing request.")
            return RpcResult(
                req_msg_id=request.message_id,
                result=RpcError(error_code=500, error_message="INTERNAL_SERVER_ERROR"),
            )

        result = task_result.return_value
        if not isinstance(self.server.broker.result_backend, InmemoryResultBackend):
            result = RpcResponse.read(BytesIO(bytes.fromhex(result)))
        if not isinstance(result, RpcResponse):
            logger.error(f"Got response from worker that is not a RpcResponse object: {result}")
            return RpcResult(
                req_msg_id=request.message_id,
                result=RpcError(error_code=500, error_message="INTERNAL_SERVER_ERROR"),
            )

        # logger.trace(f"Got RpcResponse from worker: {result!r}")

        if result.transport_error is not None:
            raise Disconnection(result.transport_error or None)
        if result.refresh_auth:
            await session.refresh_auth_maybe(True)
            await session.fetch_layer()

        return result.obj

    async def propagate(self, request: Message, session: Session) -> RpcResult | None:
        try:
            if (result := await self._process_request(request, session)) is not None:
                await session.enqueue(result, True)
            return result
        finally:
            await session.mark_rpc_completed(request.message_id)
