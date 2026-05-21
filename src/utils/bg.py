"""Strong-reference registry for fire-and-forget asyncio tasks.

`asyncio.create_task(coro)` keeps only a weak reference to the task. The
GC can collect it mid-await, which on CPython surfaces as
``Task was destroyed but it is pending!`` and silently kills your
work. Stash the task here to keep it alive until it finishes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from src.utils.logger import logger

_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.opt(exception=exc).error(
            "Background task {} failed", task.get_name()
        )
