"""Async concurrency control task runner interface."""

from __future__ import annotations

from typing import Awaitable, Callable


async def run_tasks(
    tasks: list[Callable[[], Awaitable[None]]],
    max_concurrent: int,
) -> None:
    """Run a collection of async task callables with concurrency bounded by max_concurrent.

    Args:
        tasks: List of 0-argument async callables returning Awaitable[None].
        max_concurrent: Maximum number of tasks allowed to execute concurrently (must be >= 1).

    Raises:
        ValueError: If max_concurrent is not a positive integer (<= 0 or invalid type).
    """
    raise NotImplementedError("run_tasks is not implemented")
