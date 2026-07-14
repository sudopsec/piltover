import asyncio

import pytest

from piltover.app.utils.group_calls import (
    cancel_scheduled_leave_group_calls,
    schedule_leave_group_calls_for_user,
)
from piltover.session.session_manager import SessionManager


@pytest.mark.asyncio
async def test_scheduled_leave_can_be_cancelled() -> None:
    cancelled = asyncio.Event()

    async def fake_leave(user_id: int) -> None:
        await asyncio.sleep(60)
        cancelled.set()

    import piltover.app.utils.group_calls as gc

    original = gc.leave_all_group_calls_for_user
    gc.leave_all_group_calls_for_user = fake_leave
    gc._GROUP_CALL_DISCONNECT_GRACE = 0.05
    try:
        schedule_leave_group_calls_for_user(42)
        await asyncio.sleep(0.01)
        cancel_scheduled_leave_group_calls(42)
        await asyncio.sleep(0.1)
        assert not cancelled.is_set()
    finally:
        gc.leave_all_group_calls_for_user = original
        cancel_scheduled_leave_group_calls(42)


def test_has_connected_session_for_user() -> None:
    assert SessionManager.has_connected_session_for_user(999999) is False