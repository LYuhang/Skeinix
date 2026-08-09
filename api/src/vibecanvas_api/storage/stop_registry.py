# -*- coding: utf-8 -*-
"""Process-shared execution stop-event registry.

The cancel signal is an in-process, in-memory event. It is not persisted.
Precedent for a module-level lock + dict living in its own module:
``streaming/locks``.

The ``threading.Lock`` here is *not* a DI-serialization lock (rule #5) —
it only guards the in-memory dict and is required for correctness.
"""

from __future__ import annotations

import multiprocessing
import multiprocessing.synchronize
import threading
from typing import Any, Optional

_spawn_ctx = multiprocessing.get_context("spawn")

# MODULE-level so an Event registered by ``start_execution`` is visible to
# workflow-scoped cancel/status calls in the same API process. The lock guards
# the dict only.
_STOP_EVENTS: dict[str, Any] = {}
_STOP_LOCK = threading.Lock()


def register(exec_id: str, *, use_mp: bool = False) -> Any:
    """Register (or return the existing) stop Event for ``exec_id``.

    If ``use_mp`` and an existing non-multiprocessing Event is present,
    it is replaced with a spawn-context Event (preserving a set state) —
    a process-boundary execution needs an mp Event the child can see.
    Returns the live Event for the exec_id.
    """
    with _STOP_LOCK:
        existing = _STOP_EVENTS.get(exec_id)
        if existing is None:
            ev = _spawn_ctx.Event() if use_mp else threading.Event()
            _STOP_EVENTS[exec_id] = ev
            return ev
        if use_mp and not isinstance(
            existing, multiprocessing.synchronize.Event
        ):
            replacement = _spawn_ctx.Event()
            if existing.is_set():
                replacement.set()
            _STOP_EVENTS[exec_id] = replacement
            return replacement
        return existing


def get(exec_id: str) -> Optional[Any]:
    """Return the stop Event for ``exec_id`` or ``None`` if unknown."""
    with _STOP_LOCK:
        return _STOP_EVENTS.get(exec_id)


def signal(exec_id: str) -> None:
    """Set the stop Event for ``exec_id`` (creating one if absent so a
    cancel arriving before registration is not lost)."""
    with _STOP_LOCK:
        ev = _STOP_EVENTS.get(exec_id)
        if ev is None:
            ev = threading.Event()
            _STOP_EVENTS[exec_id] = ev
    ev.set()


def discard(exec_id: str) -> None:
    """Remove the stop Event for ``exec_id`` (pop-with-default)."""
    with _STOP_LOCK:
        _STOP_EVENTS.pop(exec_id, None)
