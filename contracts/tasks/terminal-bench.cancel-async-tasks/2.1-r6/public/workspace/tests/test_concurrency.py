"""Public unit tests for async concurrency pool implementation."""

from __future__ import annotations

import asyncio
import unittest

from run import run_tasks


class TestRunTasks(unittest.IsolatedAsyncioTestCase):
    async def test_empty_tasks(self) -> None:
        """Ensure empty task list returns immediately without errors."""
        await run_tasks([], 3)

    async def test_invalid_max_concurrent(self) -> None:
        """Ensure invalid concurrency limits raise ValueError."""
        with self.assertRaises(ValueError):
            await run_tasks([], 0)
        with self.assertRaises(ValueError):
            await run_tasks([], -1)
        with self.assertRaises(ValueError):
            await run_tasks([], 2.5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            await run_tasks([], "2")  # type: ignore[arg-type]

    async def test_basic_concurrency(self) -> None:
        """Ensure tasks execute and complete with bounded concurrency."""
        active = 0
        max_active = 0
        completed = 0
        lock = asyncio.Lock()

        async def sample_task() -> None:
            nonlocal active, max_active, completed
            async with lock:
                active += 1
                if active > max_active:
                    max_active = active
            try:
                await asyncio.sleep(0.01)
                async with lock:
                    completed += 1
            finally:
                async with lock:
                    active -= 1

        tasks = [sample_task for _ in range(6)]
        await run_tasks(tasks, 2)
        self.assertEqual(completed, 6)
        self.assertLessEqual(max_active, 2)


if __name__ == "__main__":
    unittest.main()
