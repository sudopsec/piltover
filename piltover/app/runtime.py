from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from piltover.worker import Worker

_worker: Worker | None = None


def set_worker(worker: Worker | None) -> None:
    global _worker
    _worker = worker


def get_worker() -> Worker | None:
    return _worker