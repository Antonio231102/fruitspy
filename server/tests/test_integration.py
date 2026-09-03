import asyncio
import socket
import struct
import unittest
from dataclasses import replace

from fruitspy.availability_qr import AvailabilityQRProtocol
from fruitspy.crypto import gsseckey
from fruitspy.natneg import MAGIC, NN_CONNECT, NN_INIT, NatNegProtocol
from fruitspy.peerchat import PeerChatServer
from fruitspy.server_browser import ServerBrowserServer
from fruitspy.state import ServerState
from tests.helpers import internet_test_config, test_config
from tests.test_peerchat import EncryptedPeerClient
from tests.test_protocols import (
    decrypt_server_browser,
    decrypt_server_browser_stream,
    server_browser_request,
)


class SyntheticLANFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.config = test_config()
        self.state = ServerState(120, 60)
        loop = asyncio.get_running_loop()
        self.qr_transport, _ = await loop.create_datagram_endpoint(
            lambda: AvailabilityQRProtocol(self.config, self.state),
            local_addr=("127.0.0.1", 0),
        )
        self.qr_port = self.qr_transport.get_extra_info("sockname")[1]
        self.nat_transport, _ = await loop.create_datagram_endpoint(
            lambda: NatNegProtocol(self.config, self.state),
            local_addr=("127.0.0.1", 0),
        )
        self.nat_port = self.nat_transport.get_extra_info("sockname")[1]
        self.chat_service = PeerChatServer(self.config)
        self.chat_listener = await asyncio.start_server(
            self.chat_service.handle,
            "127.0.0.1",
            0,
        )
        self.chat_port = self.chat_listener.sockets[0].getsockname()[1]
        self.browser_service = ServerBrowserServer(self.config, self.state)
        self.browser_listener = await asyncio.start_server(
            self.browser_service.handle,
            "127.0.0.1",
            0,
        )
        self.browser_port = self.browser_listener.sockets[0].getsockname()[1]
        self.sockets: list[socket.socket] = []

    async def asyncTearDown(self) -> None:
        for sock in self.sockets:
            sock.close()
        self.chat_listener.close()
        self.browser_listener.close()
        self.qr_transport.close()
        self.nat_transport.close()
        await self.chat_listener.wait_closed()
        await self.browser_listener.wait_closed()

    def udp_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        self.sockets.append(sock)
        return sock

    async def exchange_udp(self, sock: socket.socket, data: bytes, port: int) -> bytes:
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(sock, data, ("127.0.0.1", port))
        response, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), 2)
        return response

    async def test_matchmaking_pipeline_reaches_peer_endpoints(self) -> None:
        availability_client = self.udp_socket()
        availability = await self.exchange_udp(
            availability_client,
            b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
            self.qr_port,
        )
        self.assertEqual(availability, b"\xfe\xfd\x09" + b"\x00" * 8)

        host_qr = self.udp_socket()
        instance = b"HOST"
        heartbeat = (
            b"\x03"
            + instance
            + b"gamename\x00FruitNinjaand\x00"
            + b"hostname\x00Synthetic Host\x00"
            + b"hostport\x006500\x00"
            + b"localip0\x00127.0.0.1\x00"
            + b"localport\x006500\x00"
            + b"maxplayers\x002\x00"
            + b"numplayers\x001\x00"
            + b"gamemode\x00openstaging\x00"
            + b"natneg\x001\x00\x00"
        )
        challenge_packet = await self.exchange_udp(host_qr, heartbeat, self.qr_port)
        challenge = challenge_packet[7:-1].decode("ascii")
        registered = await self.exchange_udp(
            host_qr,
            b"\x01" + instance + gsseckey(challenge, "nNfhSl").encode("ascii") + b"\x00",
            self.qr_port,
        )
        self.assertEqual(registered[:3], b"\xfe\xfd\x0a")

        challenge8 = b"LANMATCH"
        browser_request = server_browser_request(
            challenge8,
            "\\hostname\\maxplayers\\numplayers\\gamemode\\natneg",
        )
        browser_reader, browser_writer = await asyncio.open_connection(
            "127.0.0.1", self.browser_port
        )
        browser_writer.write(browser_request)
        await browser_writer.drain()
        encrypted_list = await asyncio.wait_for(browser_reader.read(4096), 2)
        browser_writer.close()
        await browser_writer.wait_closed()
        server_list = decrypt_server_browser(encrypted_list, challenge8)
        self.assertIn(b"Synthetic Host\x00", server_list)
        self.assertIn(b"openstaging\x00", server_list)
        self.assertIn(struct.pack(">H", 6500), server_list)


        first_chat = await EncryptedPeerClient.connect(self.chat_port)
        second_chat = await EncryptedPeerClient.connect(self.chat_port)
        try:
            for client, nick in ((first_chat, "host"), (second_chat, "joiner")):
                await client.send(f"NICK {nick}")
                await client.send(f"USER {nick} 0 * :{nick}")
                await client.read_until(f"376 {nick}")
            channel = "#GSP!FruitNinjaand!synthetic"
            await first_chat.send(f"JOIN {channel}")
            await first_chat.read_until("End of NAMES list")
            await second_chat.send(f"JOIN {channel}")
            await second_chat.read_until("End of NAMES list")
            await first_chat.read_until("joiner!joiner@")
            self.assertEqual(len(self.chat_service.channels[channel.casefold()].users), 2)
        finally:
            await first_chat.close()
            await second_chat.close()

        first_nat = self.udp_socket()
        second_nat = self.udp_socket()
        cookie = b"LAN1"
        first_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 1)) + b"\x00" * 6
        second_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 1)) + b"\x00" * 6
        first_ack = await self.exchange_udp(first_nat, first_init, self.nat_port)
        self.assertEqual(first_ack[7], 1)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(second_nat, second_init, ("127.0.0.1", self.nat_port))
        first_connect, _ = await asyncio.wait_for(loop.sock_recvfrom(first_nat, 2048), 2)
        second_packets = []
        while len(second_packets) < 2:
            packet, _ = await asyncio.wait_for(loop.sock_recvfrom(second_nat, 2048), 2)
            second_packets.append(packet)
        self.assertEqual(first_connect[7], NN_CONNECT)
        self.assertIn(NN_CONNECT, [packet[7] for packet in second_packets])


    async def test_server_info_request_returns_full_rules(self) -> None:
        source = ("10.0.0.10", 6500)
        self.state.report_server(
            source,
            b"INFO",
            {
                "gamename": "FruitNinjaandam",
                "hostname": "Automatch Host",
                "hostport": "6500",
                "numplayers": "1",
                "maxplayers": "2",
                "natneg": "1",
                "localip0": source[0],
            },
        )
        self.state.register_server(source)

        challenge = b"FULLRULE"
        browser_reader, browser_writer = await asyncio.open_connection(
            "127.0.0.1",
            self.browser_port,
        )
        try:
            browser_writer.write(
                server_browser_request(
                    challenge,
                    "",
                    options=4,
                    query_game="FruitNinjaandam",
                )
            )
            await browser_writer.drain()
            initial = await asyncio.wait_for(browser_reader.read(4096), 2)
            initial_body, cipher = decrypt_server_browser_stream(initial, challenge)
            self.assertIn(socket.inet_aton(source[0]), initial_body)

            info_request = (
                struct.pack(">H", 9)
                + b"\x01"
                + socket.inet_aton(source[0])
                + struct.pack(">H", source[1])
            )
            browser_writer.write(info_request)
            await browser_writer.drain()
            encrypted_info = await asyncio.wait_for(browser_reader.read(4096), 2)
            info = cipher.decrypt(encrypted_info)
            self.assertEqual(struct.unpack_from(">H", info)[0], len(info))
            self.assertTrue(info[3] & 2)
            self.assertEqual(info[8:12], socket.inet_aton(source[0]))
            self.assertEqual(info[2], 2)
            self.assertTrue(info[3] & 128)
            self.assertIn(b"gamename\x00FruitNinjaandam\x00", info)
            self.assertIn(b"hostname\x00Automatch Host\x00", info)
        finally:
            browser_writer.close()
            await browser_writer.wait_closed()

    async def test_server_message_request_relays_udp_payload(self) -> None:
        target = self.udp_socket()
        target_address = target.getsockname()
        self.state.report_server(
            target_address,
            b"RLY1",
            {
                "gamename": "FruitNinjaandam",
                "hostport": str(target_address[1]),
                "localip0": target_address[0],
                "localport": str(target_address[1]),
            },
        )
        self.state.register_server(target_address)

        reader, writer = await asyncio.open_connection("127.0.0.1", self.browser_port)
        try:
            payload = b"\xfd\xfc\x1e\x66\x6a\xb2LAN1"
            request = (
                struct.pack(">H", 9 + len(payload))
                + b"\x02"
                + socket.inet_aton(target_address[0])
                + struct.pack(">H", target_address[1])
                + payload
            )
            writer.write(request)
            await writer.drain()
            received, _ = await asyncio.wait_for(
                asyncio.get_running_loop().sock_recvfrom(target, 2048),
                2,
            )
            self.assertEqual(received, payload)
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_internet_relay_targets_observed_qr_endpoint(self) -> None:
        target = self.udp_socket()
        target_address = target.getsockname()
        state = ServerState(120, 60)
        state.report_server(
            target_address,
            b"INET",
            {
                "gamename": "FruitNinjaandam",
                "hostport": "6500",
                "localip0": "10.0.0.10",
                "localport": "6500",
            },
        )
        state.register_server(target_address)
        service = ServerBrowserServer(internet_test_config(), state)
        listener = await asyncio.start_server(service.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            payload = b"\xfd\xfc\x1e\x66\x6a\xb2INET"
            request = (
                struct.pack(">H", 9 + len(payload))
                + b"\x02"
                + socket.inet_aton(target_address[0])
                + struct.pack(">H", target_address[1])
                + payload
            )
            writer.write(request)
            await writer.drain()
            received, _ = await asyncio.wait_for(
                asyncio.get_running_loop().sock_recvfrom(target, 2048),
                2,
            )
            self.assertEqual(received, payload)
        finally:
            writer.close()
            await writer.wait_closed()
            listener.close()
            await listener.wait_closed()

    async def test_oversized_server_browser_frame_is_rejected(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.browser_port)
        try:
            oversized = self.config.limits.server_browser_frame_bytes + 1
            writer.write(struct.pack(">H", oversized))
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b"")
        finally:
            writer.close()
            await writer.wait_closed()

    async def test_peerchat_handshake_deadline_closes_idle_connection(self) -> None:
        config = replace(
            self.config,
            timeouts=replace(
                self.config.timeouts,
                peerchat_handshake_seconds=1,
            ),
        )
        service = PeerChatServer(config)
        listener = await asyncio.start_server(service.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b"")
        finally:
            writer.close()
            await writer.wait_closed()
            listener.close()
            await listener.wait_closed()

    async def test_server_browser_idle_deadline_closes_connection(self) -> None:
        config = replace(
            self.config,
            timeouts=replace(
                self.config.timeouts,
                server_browser_idle_seconds=1,
            ),
        )
        service = ServerBrowserServer(config, self.state)
        listener = await asyncio.start_server(service.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b"")
        finally:
            writer.close()
            await writer.wait_closed()
            listener.close()
            await listener.wait_closed()

    async def test_peerchat_connection_limit_rejects_excess_client(self) -> None:
        config = replace(
            self.config,
            limits=replace(
                self.config.limits,
                peerchat_connections=1,
                connections_per_source=1,
            ),
        )
        service = PeerChatServer(config)
        listener = await asyncio.start_server(service.handle, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        first_reader, first_writer = await asyncio.open_connection("127.0.0.1", port)
        second_reader = None
        second_writer = None
        try:
            await asyncio.sleep(0)
            second_reader, second_writer = await asyncio.open_connection(
                "127.0.0.1",
                port,
            )
            self.assertEqual(
                await asyncio.wait_for(second_reader.read(1), 1),
                b"",
            )
            self.assertFalse(first_reader.at_eof())
        finally:
            if second_writer is not None:
                second_writer.close()
                await second_writer.wait_closed()
            first_writer.close()
            await first_writer.wait_closed()
            listener.close()
            await listener.wait_closed()

    async def test_qr_rate_limit_drops_excess_datagram(self) -> None:
        config = replace(
            self.config,
            limits=replace(
                self.config.limits,
                udp_packets_per_second=1,
                udp_burst=1,
            ),
        )
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: AvailabilityQRProtocol(config, self.state),
            local_addr=("127.0.0.1", 0),
        )
        port = transport.get_extra_info("sockname")[1]
        client = self.udp_socket()
        request = b"\x09\x00\x00\x00\x00FruitNinjaand\x00"
        try:
            response = await self.exchange_udp(client, request, port)
            self.assertEqual(response, b"\xfe\xfd\x09" + b"\x00" * 8)
            await loop.sock_sendto(client, request, ("127.0.0.1", port))
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(loop.sock_recvfrom(client, 2048), 0.2)
        finally:
            transport.close()


if __name__ == "__main__":
    unittest.main()
