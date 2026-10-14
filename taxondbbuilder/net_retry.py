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
    retry_if: Callable[[Exception], bool] | None = None,
    delay_for: Callable[[Exception, int], float] | None = None,
) -> T:
    """Run ``op``, retrying only the failures the caller's policy accepts.

    ``retry_if`` vetoes a retry for one failure (a 403 will not become a 200 on
    the fourth try). ``delay_for`` replaces the linear backoff for that failure,
    which is how a server ``Retry-After`` gets honoured. Both default to the
    behaviour callers had before: retry every ``retry_on`` failure, waiting
    ``backoff_sec * (attempt + 1)``.
    """
    for attempt in range(retries + 1):
        try:
            return op()
        except retry_on as exc:
            if retry_if is not None and not retry_if(exc):
                raise wrap(exc) from exc
            if attempt >= retries:
                raise wrap(exc) from exc
            if delay_for is not None:
                delay = delay_for(exc, attempt)
                if delay > 0:
                    time.sleep(delay)
            elif backoff_sec > 0:
                time.sleep(backoff_sec * (attempt + 1))
