import asyncio
import unittest
from dataclasses import replace

from fruitspy.availability_qr import AvailabilityQRProtocol
from fruitspy.healthcheck import check_server
from fruitspy.natneg import NatNegProtocol
from fruitspy.peerchat import PeerChatServer
from fruitspy.server_browser import ServerBrowserServer
from fruitspy.state import ServerState
from tests.helpers import test_config


class HealthCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_protocol_listeners_report_healthy(self) -> None:
        config = test_config()
        state = ServerState(120, 60)
        loop = asyncio.get_running_loop()
        qr_transport, _ = await loop.create_datagram_endpoint(
            lambda: AvailabilityQRProtocol(config, state),
            local_addr=("127.0.0.1", 0),
        )
        nat_transport, _ = await loop.create_datagram_endpoint(
            lambda: NatNegProtocol(config, state),
            local_addr=("127.0.0.1", 0),
        )
        chat_service = PeerChatServer(config)
        chat_listener = await asyncio.start_server(
            chat_service.handle,
            "127.0.0.1",
            0,
        )
        browser_service = ServerBrowserServer(config, state)
        browser_listener = await asyncio.start_server(
            browser_service.handle,
            "127.0.0.1",
            0,
        )
        health_config = replace(
            config,
            ports=replace(
                config.ports,
                availability_qr_udp=qr_transport.get_extra_info("sockname")[1],
                peerchat_tcp=chat_listener.sockets[0].getsockname()[1],
                server_browser_tcp=browser_listener.sockets[0].getsockname()[1],
                natneg_udp=nat_transport.get_extra_info("sockname")[1],
            ),
        )

        try:
            results = await asyncio.to_thread(
                check_server,
                health_config,
                "127.0.0.1",
                1,
            )
            self.assertEqual([result.service for result in results], [
                "availability_qr_udp",
                "peerchat_tcp",
                "server_browser_tcp",
                "natneg_udp",
            ])
            self.assertTrue(all(result.healthy for result in results), results)
        finally:
            chat_listener.close()
            browser_listener.close()
            qr_transport.close()
            nat_transport.close()
            await chat_listener.wait_closed()
            await browser_listener.wait_closed()


if __name__ == "__main__":
    unittest.main()
