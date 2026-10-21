import time
from collections.abc import Callable, Hashable


class TTLCache[T]:
    """Small in-process TTL cache. One instance per process; not shared across workers."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[Hashable, tuple[float, T]] = {}

    def get(self, key: Hashable) -> T | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: Hashable, value: T) -> None:
        self._items[key] = (self._clock() + self._ttl, value)

    def clear(self) -> None:
        self._items.clear()
