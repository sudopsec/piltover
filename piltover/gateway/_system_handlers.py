from __future__ import annotations

from asyncio import get_running_loop
from datetime import datetime, UTC
from time import time
from typing import TYPE_CHECKING, Awaitable, Callable

from loguru import logger

from piltover.db.models import UserAuthorization, AuthKey
from piltover.tl import InitConnection, MsgsAck, MsgsStateReq, MsgsStateInfo, Ping, Pong, PingDelayDisconnect, \
    InvokeWithLayer, InvokeAfterMsg, InvokeAfterMsgs, InvokeWithoutUpdates, RpcDropAnswer, DestroySession, \
    DestroySessionOk, RpcAnswerUnknown, GetFutureSalts, FutureSalt, Long, RpcError
from piltover.tl.core_types import Message, RpcResult, FutureSalts

if TYPE_CHECKING:
    from piltover.gateway import Client
    from piltover.session import Session


async def msgs_ack(_1: Client, request: Message[MsgsAck], session: Session) -> None:
    session.ack_outbound(request.obj.msg_ids)


async def msgs_state_req(_1: Client, request: Message[MsgsStateReq], _2: Session) -> MsgsStateInfo:
    # 4 = message received (see core.telegram.org/mtproto/service_messages_about_messages)
    info = "\x04" * len(request.obj.msg_ids)
    return MsgsStateInfo(req_msg_id=request.message_id, info=info)


async def ping(_1: Client, request: Message[Ping], _2: Session) -> Pong:
    return Pong(msg_id=request.message_id, ping_id=request.obj.ping_id)


async def ping_delay_disconnect(client: Client, request: Message[PingDelayDisconnect], _: Session) -> Pong:
    if client.disconnect_timeout is not None and request.obj.disconnect_delay > 0:
        client.disconnect_timeout.reschedule(get_running_loop().time() + request.obj.disconnect_delay)

    return Pong(msg_id=request.message_id, ping_id=request.obj.ping_id)


async def _invoke_inner_query(client: Client, request: Message, session: Session) -> RpcResult | None:
    return await client._process_request(
        Message(
            obj=request.obj.query,
            message_id=request.message_id,
            seq_no=request.seq_no,
        ),
        session,
    )


def _invoke_after_wait_timeout() -> float:
    from piltover.config import GATEWAY_CONFIG

    if GATEWAY_CONFIG is None:
        return 600.0
    return GATEWAY_CONFIG.task_result_slow_timeout


async def _wait_for_invoke_after(session: Session, msg_ids: list[int]) -> RpcResult | None:
    timeout = _invoke_after_wait_timeout()
    for msg_id in msg_ids:
        if not await session.wait_for_rpc(msg_id, timeout):
            return RpcResult(
                req_msg_id=0,
                result=RpcError(error_code=420, error_message="MSG_WAIT_TIMEOUT"),
            )
    return None


async def invoke_with_layer(client: Client, request: Message[InvokeWithLayer], session: Session) -> RpcResult:
    from piltover.tl.layer_info import layer as max_layer

    layer = min(request.obj.layer, max_layer)
    if layer != session.layer:
        logger.trace(f"saving layer for key {session.auth_data.perm_auth_key_id}")
        await AuthKey.filter(id=session.auth_data.perm_auth_key_id).update(layer=layer)
    session.layer = layer
    return await _invoke_inner_query(client, request, session)


async def invoke_after_msg(client: Client, request: Message[InvokeAfterMsg], session: Session) -> RpcResult:
    if (err := await _wait_for_invoke_after(session, [request.obj.msg_id])) is not None:
        err.req_msg_id = request.message_id
        return err
    return await _invoke_inner_query(client, request, session)


async def invoke_after_msgs(client: Client, request: Message[InvokeAfterMsgs], session: Session) -> RpcResult:
    if (err := await _wait_for_invoke_after(session, request.obj.msg_ids)) is not None:
        err.req_msg_id = request.message_id
        return err
    return await _invoke_inner_query(client, request, session)


async def invoke_without_updates(client: Client, request: Message[InvokeWithoutUpdates], session: Session) -> RpcResult:
    session.no_updates = True
    return await _invoke_inner_query(client, request, session)


async def init_connection(client: Client, request: Message[InitConnection], session: Session) -> RpcResult:
    # hmm yes yes, I trust you client
    # the api id is always correct, it has always been!

    if session.auth_id and not session.had_init_connection:
        session.had_init_connection = True
        await UserAuthorization.filter(id=session.auth_id).update(
            active_at=datetime.now(UTC),
            device_model=request.obj.device_model,
            system_version=request.obj.system_version,
            app_version=request.obj.app_version,
            ip=client.peername[0],
        )

    if not session.no_updates:
        ...  # TODO: subscribe user to updates manually

    logger.info("initConnection with Api ID: {api_id}", api_id=request.obj.api_id)

    return await _invoke_inner_query(client, request, session)


async def destroy_session(_1: Client, request: Message[DestroySession], session: Session) -> DestroySessionOk:
    from piltover.session import SessionManager

    if session.auth_data is not None and session.auth_data.auth_key_id is not None:
        uniq_id = session.auth_data.auth_key_id, request.obj.session_id
        if (stored := SessionManager.sessions.get(uniq_id)) is not None:
            SessionManager.finalize(stored)
    return DestroySessionOk(session_id=request.obj.session_id)


async def rpc_drop_answer(_1: Client, request: Message[RpcDropAnswer], _3: Session) -> RpcResult:
    return RpcResult(req_msg_id=request.message_id, result=RpcAnswerUnknown())


async def get_future_salts(client: Client, request: Message[GetFutureSalts], session: Session) -> FutureSalts:
    limit = min(max(request.obj.num, 1), 64)
    now = int(time())
    base_timestamp = now // (30 * 60)

    return FutureSalts(
        req_msg_id=request.message_id,
        now=now,
        salts=[
            FutureSalt(
                valid_since=(base_timestamp + salt_offset) * 30 * 60,
                valid_until=(base_timestamp + salt_offset + 1) * 30 * 60,
                salt=Long.read_bytes(session.make_salt(
                    client.server.salt_key, session.auth_data.auth_key_id, base_timestamp + salt_offset,
                )),
            )
            for salt_offset in range(limit)
        ]
    )


SYSTEM_HANDLERS: dict[int, Callable[[Client, Message, Session], Awaitable[RpcResult | Pong | MsgsStateInfo | None]]] = {
    MsgsAck.tlid(): msgs_ack,
    MsgsStateReq.tlid(): msgs_state_req,
    Ping.tlid(): ping,
    PingDelayDisconnect.tlid(): ping_delay_disconnect,
    InvokeWithLayer.tlid(): invoke_with_layer,
    InvokeAfterMsg.tlid(): invoke_after_msg,
    InvokeAfterMsgs.tlid(): invoke_after_msgs,
    InvokeWithoutUpdates.tlid(): invoke_without_updates,
    InitConnection.tlid(): init_connection,
    DestroySession.tlid(): destroy_session,
    RpcDropAnswer.tlid(): rpc_drop_answer,
    GetFutureSalts.tlid(): get_future_salts,
}
