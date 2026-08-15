#!/usr/bin/env python3
"""Tamper probe submitting flawed implementation using shield(gather(...)) that returns before cleanup upon cancellation."""

from __future__ import annotations

import sys

TAMPER_CODE = """\"\"\"Flawed concurrency implementation using shield(gather(...)) that returns before cancellation cleanup.\"\"\"

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


async def run_tasks(
    tasks: list[Callable[[], Awaitable[None]]],
    max_concurrent: int,
) -> None:
    \"\"\"Flawed implementation that returns prematurely before worker cleanup finishes upon cancellation.\"\"\"
    if not isinstance(max_concurrent, int) or isinstance(max_concurrent, bool) or max_concurrent < 1:
        raise ValueError("max_concurrent must be a positive integer")

    if not tasks:
        return

    sem = asyncio.Semaphore(max_concurrent)

    async def worker(t: Callable[[], Awaitable[None]]) -> None:
        async with sem:
            await t()

    task_objs = [asyncio.create_task(worker(t)) for t in tasks]
    try:
        # Flaw: awaiting shield(gather(...)) causes CancelledError to be raised to run_tasks immediately
        # without waiting for in-flight task cleanups to complete!
        await asyncio.shield(asyncio.gather(*task_objs))
    except asyncio.CancelledError:
        for t in task_objs:
            t.cancel()
        raise
"""


def main() -> int:
    sys.stdout.write(TAMPER_CODE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
