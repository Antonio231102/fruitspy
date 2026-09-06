from __future__ import annotations

import argparse
import signal
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from .admission import create_udp_admission
from .availability_qr import AvailabilityQRProtocol
from .config import ServerConfig, load_config
from .drain import DrainController
from .metrics import MetricsHttpServer, MetricsRegistry
from .natneg import NatNegProtocol
from .peerchat import PeerChatServer
from .server_browser import ServerBrowserServer
from .state import ServerState

LOG = logging.getLogger("fruitspy")


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop,
    drain: DrainController,
) -> Callable[[], None]:
    loop_signals: list[signal.Signals] = []
    fallback_handlers: list[tuple[signal.Signals, object]] = []
    for watched_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(watched_signal, drain.request)
        except NotImplementedError:
            previous = signal.signal(
                watched_signal,
                lambda *_: loop.call_soon_threadsafe(drain.request),
            )
            fallback_handlers.append((watched_signal, previous))
        else:
            loop_signals.append(watched_signal)

    def restore() -> None:
        for watched_signal in loop_signals:
            loop.remove_signal_handler(watched_signal)
        for watched_signal, previous in fallback_handlers:
            signal.signal(watched_signal, previous)

    return restore


async def run_server(config: ServerConfig) -> None:
    loop = asyncio.get_running_loop()
    metrics = MetricsRegistry()
    drain = DrainController(metrics)
    state = ServerState(
        reported_server_ttl=config.timeouts.reported_server_seconds,
        nat_session_ttl=config.timeouts.nat_session_seconds,
        max_reported_servers=config.limits.reported_servers,
        max_nat_sessions=config.limits.nat_sessions,
        metrics=metrics,
    )
    udp_admission = create_udp_admission(config)
    qr_transport, _ = await loop.create_datagram_endpoint(
        lambda: AvailabilityQRProtocol(
            config,
            state,
            udp_admission,
            metrics,
            drain,
        ),
        local_addr=(config.bind_host, config.ports.availability_qr_udp),
    )
    nat_transport, natneg = await loop.create_datagram_endpoint(
        lambda: NatNegProtocol(
            config,
            state,
            udp_admission,
            metrics,
            drain,
        ),
        local_addr=(config.bind_host, config.ports.natneg_udp),
    )
    peerchat = PeerChatServer(config, metrics, drain)
    peerchat_listener = await asyncio.start_server(
        peerchat.handle,
        config.bind_host,
        config.ports.peerchat_tcp,
    )
    server_browser = ServerBrowserServer(config, state, metrics, drain)
    browser_listener = await asyncio.start_server(
        server_browser.handle,
        config.bind_host,
        config.ports.server_browser_tcp,
    )
    metrics_server = MetricsHttpServer(metrics)
    metrics_listener = await asyncio.start_server(
        metrics_server.handle,
        config.metrics.bind_host,
        config.metrics.port,
        limit=4096,
    )
    restore_signal_handlers = _install_signal_handlers(loop, drain)

    LOG.info("FruitSpy game=%s", config.game.name)
    LOG.info("availability/QR UDP %s:%d", config.bind_host, config.ports.availability_qr_udp)
    LOG.info("PeerChat TCP %s:%d", config.bind_host, config.ports.peerchat_tcp)
    LOG.info("Server Browser TCP %s:%d", config.bind_host, config.ports.server_browser_tcp)
    LOG.info("NatNeg UDP %s:%d", config.bind_host, config.ports.natneg_udp)
    LOG.info(
        "Metrics HTTP %s:%d",
        config.metrics.bind_host,
        config.metrics.port,
    )
    LOG.info(
        "Relay policy=%s fallback=%.1fs ttl=%ds drain=%ds",
        config.relay.policy,
        config.relay.fallback_seconds,
        config.relay.session_seconds,
        config.timeouts.drain_seconds,
    )

    service_tasks = [
        asyncio.create_task(peerchat_listener.serve_forever()),
        asyncio.create_task(browser_listener.serve_forever()),
        asyncio.create_task(metrics_listener.serve_forever()),
        asyncio.create_task(
            state.expire_periodically(
                config.timeouts.state_expiry_interval_seconds,
            )
        ),
    ]
    drain_task = asyncio.create_task(drain.wait())
    try:
        completed, _ = await asyncio.wait(
            [drain_task, *service_tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if drain_task not in completed:
            for task in completed:
                task.result()
            raise RuntimeError("FruitSpy service task stopped unexpectedly")

        peerchat_listener.close()
        browser_listener.close()
        await peerchat_listener.wait_closed()
        await browser_listener.wait_closed()
        await asyncio.gather(
            peerchat.begin_drain(),
            server_browser.begin_drain(),
        )
        active_relays = natneg.begin_drain()
        LOG.info(
            "service=server event=relay_drain_wait active_relays=%d "
            "timeout_seconds=%d",
            active_relays,
            config.timeouts.drain_seconds,
        )
        relays_drained = await natneg.wait_for_relays(
            config.timeouts.drain_seconds,
        )
        if not relays_drained:
            LOG.warning(
                "service=server event=relay_drain_timeout active_relays=%d",
                natneg.active_relays,
            )
            natneg.close_relays("server_drain_timeout")
        drain.complete(timed_out=not relays_drained)
    finally:
        peerchat_listener.close()
        browser_listener.close()
        metrics_listener.close()
        qr_transport.close()
        nat_transport.close()
        drain_task.cancel()
        for task in service_tasks:
            task.cancel()
        await asyncio.gather(drain_task, *service_tasks, return_exceptions=True)
        await peerchat_listener.wait_closed()
        await browser_listener.wait_closed()
        await metrics_listener.wait_closed()
        restore_signal_handlers()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fruitspy-server",
        description="Standalone GameSpy-compatible server for Fruit Ninja 1.7.6",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config.json",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    config = load_config(args.config)
    try:
        asyncio.run(run_server(config))
    except KeyboardInterrupt:
        LOG.info("server stopped")


if __name__ == "__main__":
    main()
