"""Small bounded async retry utility for explicitly retryable dependency failures."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    retries: int,
    retry_on: tuple[type[BaseException], ...],
    base_delay_seconds: float = 0.25,
    jitter: bool = True,
) -> T:
    """Run once plus at most ``retries`` bounded attempts."""
    for attempt in range(retries + 1):
        try:
            return await operation()
        except retry_on:
            if attempt == retries:
                raise
            delay = base_delay_seconds * (2**attempt)
            if jitter:
                delay *= random.uniform(0.5, 1.5)
            await asyncio.sleep(delay)
    raise AssertionError("retry loop must return or raise")
