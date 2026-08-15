#!/usr/bin/env python3
"""Candidate probe submitting independently designed Queue worker pool implementation."""

from __future__ import annotations

import sys

CANDIDATE_CODE = """\"\"\"Async concurrency control implementation using worker queue with shared failure state.\"\"\"

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


async def run_tasks(
    tasks: list[Callable[[], Awaitable[None]]],
    max_concurrent: int,
) -> None:
    \"\"\"Run tasks with bounded concurrency using a worker queue pool.\"\"\"
    if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool) or max_concurrent < 1:
        raise ValueError("max_concurrent must be a positive integer")

    if not tasks:
        return

    queue: asyncio.Queue[Callable[[], Awaitable[None]]] = asyncio.Queue()
    for task_fn in tasks:
        queue.put_nowait(task_fn)

    failed = False

    async def worker() -> None:
        nonlocal failed
        while not queue.empty() and not failed:
            try:
                fn = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                if failed:
                    break
                await fn()
            except BaseException:
                failed = True
                raise
            finally:
                queue.task_done()

    num_workers = min(max_concurrent, len(tasks))
    worker_tasks = [asyncio.create_task(worker()) for _ in range(num_workers)]

    try:
        await asyncio.gather(*worker_tasks)
    except BaseException:
        failed = True
        # Drain remaining queued tasks so they never start
        while not queue.empty():
            try:
                queue.get_nowait()
                queue.task_done()
            except asyncio.QueueEmpty:
                break
        for wt in worker_tasks:
            if not wt.done():
                wt.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise
"""


def main() -> int:
    sys.stdout.write(CANDIDATE_CODE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
