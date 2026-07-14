from __future__ import annotations

import asyncio
from asyncio import Event
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from piltover.auth_data import AuthData
from piltover.message_brokers.in_memory_broker import InMemoryMessageBroker
from piltover.session import Session, SessionManager
from piltover.tl import Pong


@pytest_asyncio.fixture
async def broker() -> InMemoryMessageBroker:
    message_broker = InMemoryMessageBroker()
    await message_broker.startup()
    SessionManager.set_broker(message_broker)
    yield message_broker
    await message_broker.shutdown()
    SessionManager.sessions.clear()


def _make_client_mock() -> MagicMock:
    client = MagicMock()
    client.message_available = Event()
    client._write_session_queues = AsyncMock()
    return client


def _make_session(*, client: MagicMock | None = None, session_id: int = 42) -> Session:
    auth = AuthData(auth_key_id=123, auth_key=b"x" * 256, perm_auth_key_id=123)
    session = Session(session_id, client=client, auth_data=auth)
    SessionManager.sessions[(123, session_id)] = session
    return session


@pytest.mark.asyncio
async def test_enqueue_without_connected_client(broker: InMemoryMessageBroker) -> None:
    session = _make_session()
    broker.subscribe(session)

    await session.enqueue(Pong(msg_id=1, ping_id=1), in_reply=False)

    assert session.message_queue.qsize() == 1
    assert session.client is None


@pytest.mark.asyncio
async def test_disconnect_keeps_session_in_manager(broker: InMemoryMessageBroker) -> None:
    client = _make_client_mock()
    session = _make_session(client=client)
    session.connect(client)

    session.disconnect()

    assert (123, 42) in SessionManager.sessions
    assert session.client is None
    assert broker.subscribed_sessions.get(42) is session


@pytest.mark.asyncio
async def test_enqueue_while_disconnected_then_reconnect(broker: InMemoryMessageBroker) -> None:
    client1 = _make_client_mock()
    client2 = _make_client_mock()
    session = _make_session(client=client1)
    session.connect(client1)
    session.disconnect()

    await session.enqueue(Pong(msg_id=1, ping_id=1), in_reply=False)
    assert session.message_queue.qsize() == 1

    session.connect(client2)
    assert session.client is client2
    assert session.resend_pending_on_connect is False
    client2.message_available.is_set()


@pytest.mark.asyncio
async def test_pending_outbound_resend_on_reconnect(broker: InMemoryMessageBroker) -> None:
    client1 = _make_client_mock()
    client2 = _make_client_mock()
    session = _make_session(client=client1)
    session.connect(client1)
    session.track_pending_outbound(100, 1, b"payload")
    session.disconnect()

    session.connect(client2)

    assert session.resend_pending_on_connect is True
    client2.message_available.is_set()


@pytest.mark.asyncio
async def test_ack_clears_pending(broker: InMemoryMessageBroker) -> None:
    session = _make_session()
    session.track_pending_outbound(100, 1, b"a")
    session.track_pending_outbound(200, 3, b"b")

    session.ack_outbound([100])

    assert 100 not in session.pending_outbound
    assert 200 in session.pending_outbound


@pytest.mark.asyncio
async def test_enqueue_does_not_flush_immediately(broker: InMemoryMessageBroker) -> None:
    client = _make_client_mock()
    session = _make_session()
    session.connect(client)

    await session.enqueue(Pong(msg_id=1, ping_id=1), in_reply=True)

    client._write_session_queues.assert_not_called()
    assert session.message_queue.qsize() == 1


@pytest.mark.asyncio
async def test_finalize_after_disconnect_ttl(broker: InMemoryMessageBroker, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SessionManager, "DISCONNECTED_SESSION_TTL", 0.05)
    client = _make_client_mock()
    session = _make_session(client=client)
    session.connect(client)
    broker.subscribe(session)

    session.disconnect()
    await asyncio.sleep(0.1)

    assert (123, 42) not in SessionManager.sessions
    assert session.pending_outbound == {}