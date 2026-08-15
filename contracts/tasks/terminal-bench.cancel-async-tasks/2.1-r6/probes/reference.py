#!/usr/bin/env python3
"""Reference probe submitting TaskGroup + Semaphore based implementation."""

from __future__ import annotations

import sys

REFERENCE_CODE = """\"\"\"Async concurrency control implementation using TaskGroup and Semaphore.\"\"\"

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


async def run_tasks(
    tasks: list[Callable[[], Awaitable[None]]],
    max_concurrent: int,
) -> None:
    \"\"\"Run tasks with bounded concurrency using TaskGroup and Semaphore.\"\"\"
    if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool) or max_concurrent < 1:
        raise ValueError("max_concurrent must be a positive integer")

    if not tasks:
        return

    sem = asyncio.Semaphore(max_concurrent)
    has_error = False

    async def worker(t: Callable[[], Awaitable[None]]) -> None:
        nonlocal has_error
        async with sem:
            current = asyncio.current_task()
            if has_error or (current is not None and current.cancelling() > 0):
                return
            try:
                await t()
            except BaseException:
                has_error = True
                raise

    async with asyncio.TaskGroup() as tg:
        for task_callable in tasks:
            tg.create_task(worker(task_callable))
"""


def main() -> int:
    sys.stdout.write(REFERENCE_CODE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
