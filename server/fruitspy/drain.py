from __future__ import annotations

import asyncio
import logging
import time

from .metrics import MetricsRegistry

LOG = logging.getLogger(__name__)


class DrainController:
    def __init__(self, metrics: MetricsRegistry) -> None:
        self.metrics = metrics
        self._requested = asyncio.Event()
        self._completed = False
        self.started_at: float | None = None

    @property
    def is_draining(self) -> bool:
        return self._requested.is_set()

    def request(self) -> bool:
        if self._requested.is_set():
            return False
        self.started_at = time.monotonic()
        self._requested.set()
        self.metrics.set_gauge("fruitspy_server_draining", 1)
        self.metrics.increment("fruitspy_drain_events_total", event="started")
        LOG.info("service=server event=drain_started")
        return True

    async def wait(self) -> None:
        await self._requested.wait()

    def complete(self, *, timed_out: bool) -> None:
        if self._completed:
            return
        self._completed = True
        event = "timed_out" if timed_out else "completed"
        self.metrics.increment("fruitspy_drain_events_total", event=event)
        elapsed = (
            max(0.0, time.monotonic() - self.started_at)
            if self.started_at is not None
            else 0.0
        )
        LOG.info(
            "service=server event=drain_%s duration_ms=%d",
            event,
            round(elapsed * 1000),
        )
