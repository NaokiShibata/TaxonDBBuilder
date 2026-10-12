"""Shared retry/backoff policy for network calls."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_call(
    op: Callable[[], T],
    *,
    retries: int,
    backoff_sec: float,
    retry_on: tuple[type[BaseException], ...],
    wrap: Callable[[Exception], Exception],
) -> T:
    for attempt in range(retries + 1):
        try:
            return op()
        except retry_on as exc:
            if attempt >= retries:
                raise wrap(exc) from exc
        if backoff_sec > 0:
            time.sleep(backoff_sec * (attempt + 1))
