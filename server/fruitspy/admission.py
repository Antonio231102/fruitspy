from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from .config import ServerConfig


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


class UdpAdmissionDecision(Enum):
    ALLOWED = "allowed"
    GLOBAL_RATE_LIMIT = "global_rate_limit"
    SOURCE_RATE_LIMIT = "source_rate_limit"
    SOURCE_BAN_STARTED = "source_ban_started"
    SOURCE_BANNED = "source_banned"
    SOURCE_TABLE_FULL = "source_table_full"


def udp_response_within_amplification_limit(
    request_bytes: int,
    response_bytes: int,
    *,
    numerator: int,
    denominator: int = 1,
) -> bool:
    return (
        request_bytes > 0
        and response_bytes >= 0
        and numerator > 0
        and denominator > 0
        and response_bytes * denominator <= request_bytes * numerator
    )


@dataclass(slots=True)
class _UdpSource:
    bucket: TokenBucket
    last_seen: float
    violations: int = 0
    banned_until: float = 0.0


class UdpAdmission:
    def __init__(
        self,
        source_rate_per_second: int,
        source_burst: int,
        global_rate_per_second: int,
        global_burst: int,
        max_sources: int,
        entry_ttl_seconds: int,
        source_violation_burst: int,
        source_ban_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.source_rate_per_second = source_rate_per_second
        self.source_burst = source_burst
        self.max_sources = max_sources
        self.entry_ttl_seconds = entry_ttl_seconds
        self.source_violation_burst = source_violation_burst
        self.source_ban_seconds = source_ban_seconds
        self.clock = clock
        self._global_bucket = TokenBucket(
            global_rate_per_second,
            global_burst,
            clock=clock,
        )
        self._sources: dict[str, _UdpSource] = {}
        self._next_expiry_at = self.clock() + entry_ttl_seconds

    def allow(self, source: str) -> UdpAdmissionDecision:
        now = self.clock()
        if now >= self._next_expiry_at:
            self._expire(now)
            self._next_expiry_at = now + self.entry_ttl_seconds

        source_state = self._sources.get(source)
        if source_state is not None and source_state.banned_until > now:
            return UdpAdmissionDecision.SOURCE_BANNED
        if not self._global_bucket.allow():
            return UdpAdmissionDecision.GLOBAL_RATE_LIMIT

        if source_state is None:
            if len(self._sources) >= self.max_sources:
                self._expire(now)
            if len(self._sources) >= self.max_sources:
                return UdpAdmissionDecision.SOURCE_TABLE_FULL
            source_state = _UdpSource(
                bucket=TokenBucket(
                    self.source_rate_per_second,
                    self.source_burst,
                    clock=self.clock,
                ),
                last_seen=now,
            )
            self._sources[source] = source_state
        else:
            source_state.last_seen = now

        if source_state.bucket.allow():
            source_state.violations = 0
            return UdpAdmissionDecision.ALLOWED

        source_state.violations += 1
        if source_state.violations >= self.source_violation_burst:
            source_state.violations = 0
            source_state.banned_until = now + self.source_ban_seconds
            return UdpAdmissionDecision.SOURCE_BAN_STARTED
        return UdpAdmissionDecision.SOURCE_RATE_LIMIT

    def _expire(self, now: float) -> None:
        self._sources = {
            source: state
            for source, state in self._sources.items()
            if state.banned_until > now
            or now - state.last_seen <= self.entry_ttl_seconds
        }


def create_udp_admission(config: ServerConfig) -> UdpAdmission:
    return UdpAdmission(
        source_rate_per_second=config.limits.udp_packets_per_second,
        source_burst=config.limits.udp_burst,
        global_rate_per_second=config.limits.udp_global_packets_per_second,
        global_burst=config.limits.udp_global_burst,
        max_sources=config.limits.udp_tracked_sources,
        entry_ttl_seconds=config.timeouts.rate_limit_entry_seconds,
        source_violation_burst=config.limits.udp_source_violation_burst,
        source_ban_seconds=config.timeouts.udp_source_ban_seconds,
    )


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
