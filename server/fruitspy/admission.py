from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _TokenBucket:
    tokens: float
    updated_at: float


class SourceRateLimiter:
    def __init__(
        self,
        rate_per_second: int,
        burst: int,
        max_sources: int,
        entry_ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_per_second = rate_per_second
        self.burst = burst
        self.max_sources = max_sources
        self.entry_ttl_seconds = entry_ttl_seconds
        self.clock = clock
        self._buckets: dict[str, _TokenBucket] = {}
        self._next_expiry_at = self.clock() + entry_ttl_seconds

    def allow(self, source: str) -> bool:
        now = self.clock()
        if now >= self._next_expiry_at:
            self._expire(now)
            self._next_expiry_at = now + self.entry_ttl_seconds

        bucket = self._buckets.get(source)
        if bucket is None:
            if len(self._buckets) >= self.max_sources:
                self._expire(now)
            if len(self._buckets) >= self.max_sources:
                return False
            self._buckets[source] = _TokenBucket(
                tokens=float(self.burst - 1),
                updated_at=now,
            )
            return True

        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(
            float(self.burst),
            bucket.tokens + elapsed * self.rate_per_second,
        )
        bucket.updated_at = now
        if bucket.tokens < 1.0:
            return False
        bucket.tokens -= 1.0
        return True

    def _expire(self, now: float) -> None:
        self._buckets = {
            source: bucket
            for source, bucket in self._buckets.items()
            if now - bucket.updated_at <= self.entry_ttl_seconds
        }


class ConnectionAdmission:
    def __init__(self, total_limit: int, per_source_limit: int) -> None:
        self.total_limit = total_limit
        self.per_source_limit = per_source_limit
        self.total = 0
        self._sources: dict[str, int] = {}

    def acquire(self, source: str) -> bool:
        source_count = self._sources.get(source, 0)
        if self.total >= self.total_limit or source_count >= self.per_source_limit:
            return False
        self.total += 1
        self._sources[source] = source_count + 1
        return True

    def release(self, source: str) -> None:
        source_count = self._sources.get(source)
        if source_count is None:
            return
        self.total -= 1
        if source_count == 1:
            self._sources.pop(source, None)
        else:
            self._sources[source] = source_count - 1
