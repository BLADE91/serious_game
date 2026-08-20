from __future__ import annotations

from threading import Event
from time import monotonic
from typing import Callable


StreamCancelled = Callable[[], bool] | None


def ensure_stream_open(cancelled: StreamCancelled) -> None:
    if cancelled is not None and cancelled():
        raise ConnectionAbortedError("NPC response stream disconnected")


def wait_for_stream_ack(
    acknowledged: Event,
    cancelled: StreamCancelled,
    *,
    timeout: float = 10.0,
) -> bool:
    deadline = monotonic() + timeout
    while True:
        ensure_stream_open(cancelled)
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        if acknowledged.wait(min(0.05, remaining)):
            ensure_stream_open(cancelled)
            return True
