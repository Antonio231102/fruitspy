from __future__ import annotations

import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self,
        rate_per_second: int,
        burst: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.rate_per_second = rate_per_second
        self.burst = burst
        self.clock = clock
        self.tokens = float(burst)
        self.updated_at = self.clock()

    def allow(self, cost: int = 1) -> bool:
        if cost < 1:
            raise ValueError("token cost must be positive")
        now = self.clock()
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(
            float(self.burst),
            self.tokens + elapsed * self.rate_per_second,
        )
        self.updated_at = now
        if self.tokens < cost:
            return False
        self.tokens -= cost
        return True


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
        self._buckets: dict[str, TokenBucket] = {}
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
            bucket = TokenBucket(
                self.rate_per_second,
                self.burst,
                clock=self.clock,
            )
            self._buckets[source] = bucket

        return bucket.allow()

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
