import asyncio
from collections import defaultdict, deque

from app.domain.clock import Clock


class SlidingWindowLimiter:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = self._clock.now().timestamp()
        cutoff = now - window_seconds
        async with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True
