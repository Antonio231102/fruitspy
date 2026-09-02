from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .availability_qr import AvailabilityQRProtocol
from .config import ServerConfig, load_config
from .natneg import NatNegProtocol
from .peerchat import PeerChatServer
from .server_browser import ServerBrowserServer
from .state import ServerState

LOG = logging.getLogger("fruitspy")


async def run_server(config: ServerConfig) -> None:
    loop = asyncio.get_running_loop()
    state = ServerState(
        reported_server_ttl=config.timeouts.reported_server_seconds,
        nat_session_ttl=config.timeouts.nat_session_seconds,
    )
    qr_transport, _ = await loop.create_datagram_endpoint(
        lambda: AvailabilityQRProtocol(config, state),
        local_addr=(config.bind_host, config.ports.availability_qr_udp),
    )
    nat_transport, _ = await loop.create_datagram_endpoint(
        lambda: NatNegProtocol(config, state),
        local_addr=(config.bind_host, config.ports.natneg_udp),
    )
    peerchat = PeerChatServer(config)
    peerchat_listener = await asyncio.start_server(
        peerchat.handle,
        config.bind_host,
        config.ports.peerchat_tcp,
    )
    server_browser = ServerBrowserServer(config, state)
    browser_listener = await asyncio.start_server(
        server_browser.handle,
        config.bind_host,
        config.ports.server_browser_tcp,
    )

    LOG.info(
        "FruitSpy game=%s mode=%s advertise=%s",
        config.game.name,
        config.mode,
        config.advertise_host,
    )
    LOG.info("availability/QR UDP %s:%d", config.bind_host, config.ports.availability_qr_udp)
    LOG.info("PeerChat TCP %s:%d", config.bind_host, config.ports.peerchat_tcp)
    LOG.info("Server Browser TCP %s:%d", config.bind_host, config.ports.server_browser_tcp)
    LOG.info("NatNeg UDP %s:%d", config.bind_host, config.ports.natneg_udp)

    try:
        await asyncio.gather(
            peerchat_listener.serve_forever(),
            browser_listener.serve_forever(),
        )
    finally:
        peerchat_listener.close()
        browser_listener.close()
        qr_transport.close()
        nat_transport.close()
        await peerchat_listener.wait_closed()
        await browser_listener.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fruitspy-server",
        description="Standalone GameSpy-compatible LAN server for Fruit Ninja 1.7.6",
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
