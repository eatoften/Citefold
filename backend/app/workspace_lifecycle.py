from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock


_workspace_lifecycle_lock = RLock()


@contextmanager
def workspace_lifecycle_lock() -> Iterator[None]:
    """Serialize resource lifecycle checks and their matching mutations.

    SQLite still owns durable concurrency control. This process-wide lock closes
    the in-process gap between checking active tasks and deleting, restoring, or
    purging the same resource. It is re-entrant because service operations may
    call other lifecycle-aware helpers.
    """

    with _workspace_lifecycle_lock:
        yield
