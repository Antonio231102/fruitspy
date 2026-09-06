import asyncio
import socket
import struct
import unittest
from dataclasses import replace

from fruitspy.admission import UdpAdmission, create_udp_admission
from fruitspy.availability_qr import AvailabilityQRProtocol
from fruitspy.crypto import EnctypeX, gsseckey
from fruitspy.natneg import (
    MAGIC,
    NN_CONNECT,
    NN_CONNECT_PING,
    NN_ERT_TEST,
    NN_INIT,
    NN_INIT_ACK,
    NN_REPORT,
    NN_REPORT_ACK,
    NN_NATIFY_REQUEST,
    NatNegProtocol,
)
from fruitspy.peerchat import PeerChatServer
from fruitspy.server_browser import (
    DELETE_SERVER_MESSAGE,
    PUSH_SERVER_MESSAGE,
    ServerBrowserServer,
)
from fruitspy.state import ServerState
from tests.helpers import test_config
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
        self.udp_admission = create_udp_admission(self.config)
        loop = asyncio.get_running_loop()
        self.qr_transport, self.qr_protocol = await loop.create_datagram_endpoint(
            lambda: AvailabilityQRProtocol(
                self.config,
                self.state,
                self.udp_admission,
            ),
            local_addr=("127.0.0.1", 0),
        )
        self.qr_port = self.qr_transport.get_extra_info("sockname")[1]
        self.nat_transport, self.nat_protocol = await loop.create_datagram_endpoint(
            lambda: NatNegProtocol(
                self.config,
                self.state,
                self.udp_admission,
            ),
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

    async def receive_udp_type(
        self,
        sock: socket.socket,
        packet_type: int,
        timeout: float = 2,
    ) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            packet, _ = await asyncio.wait_for(
                loop.sock_recvfrom(sock, 4096),
                deadline - loop.time(),
            )
            if len(packet) > 7 and packet[7] == packet_type:
                return packet

    async def read_browser_frame(
        self,
        reader: asyncio.StreamReader,
        cipher: EnctypeX,
    ) -> bytes:
        encrypted_header = await asyncio.wait_for(reader.readexactly(2), 2)
        header = cipher.decrypt(encrypted_header)
        size = struct.unpack(">H", header)[0]
        encrypted_payload = await asyncio.wait_for(reader.readexactly(size - 2), 2)
        return header + cipher.decrypt(encrypted_payload)

    async def pair_nat_clients(self, cookie: bytes) -> tuple[socket.socket, socket.socket]:
        first = self.udp_socket()
        second = self.udp_socket()
        first_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 0)) + b"\x00" * 6
        second_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 0)) + b"\x00" * 6
        first_ack = await self.exchange_udp(first, first_init, self.nat_port)
        self.assertEqual(first_ack[7], NN_INIT_ACK)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(second, second_init, ("127.0.0.1", self.nat_port))
        await self.receive_udp_type(first, NN_CONNECT)
        second_packets = [
            await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 2),
            await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 2),
        ]
        self.assertEqual(
            {packet[0][7] for packet in second_packets},
            {NN_INIT_ACK, NN_CONNECT},
        )
        return first, second

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

    async def test_push_updates_break_simultaneous_same_egress_host_race(self) -> None:
        browsers = []
        for challenge in (b"SAMEIPA1", b"SAMEIPB2"):
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                self.browser_port,
            )
            writer.write(
                server_browser_request(
                    challenge,
                    "",
                    options=4,
                    query_game="FruitNinjaandam",
                )
            )
            await writer.drain()
            encrypted_list = await asyncio.wait_for(reader.read(4096), 2)
            initial_list, cipher = decrypt_server_browser_stream(
                encrypted_list,
                challenge,
            )
            self.assertTrue(initial_list.endswith(b"\x00\xff\xff\xff\xff"))
            self.assertEqual(initial_list[8:], b"\x00\xff\xff\xff\xff")
            browsers.append((reader, writer, cipher))

        public_ip = "198.51.100.40"
        first_source = (public_ip, 41001)
        second_source = (public_ip, 41002)
        first_keys = {
            "gamename": "FruitNinjaandam",
            "hostname": "First Host",
            "hostport": "6500",
            "localip0": "192.168.1.10",
            "localport": "6500",
            "maxplayers": "2",
            "numplayers": "1",
            "gamemode": "openstaging",
            "natneg": "1",
        }
        second_keys = {
            **first_keys,
            "hostname": "Second Host",
            "localip0": "192.168.1.11",
        }

        try:
            self.state.report_server(first_source, b"HOST", first_keys)
            self.state.register_server(first_source)
            for reader, _, cipher in browsers:
                pushed = await self.read_browser_frame(reader, cipher)
                self.assertEqual(pushed[2], PUSH_SERVER_MESSAGE)
                self.assertIn(socket.inet_aton(public_ip), pushed)
                self.assertIn(socket.inet_aton("192.168.1.10"), pushed)
                self.assertIn(b"First Host\x00", pushed)

            self.state.report_server(second_source, b"JOIN", second_keys)
            self.state.register_server(second_source)
            for reader, _, _ in browsers:
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(reader.readexactly(1), 0.05)

            self.state.report_server(
                first_source,
                b"HOST",
                {**first_keys, "numplayers": "2"},
            )
            for reader, _, cipher in browsers:
                deleted = await self.read_browser_frame(reader, cipher)
                replacement = await self.read_browser_frame(reader, cipher)
                self.assertEqual(deleted[2], DELETE_SERVER_MESSAGE)
                self.assertEqual(deleted[3:7], socket.inet_aton(public_ip))
                self.assertEqual(struct.unpack_from(">H", deleted, 7)[0], 41001)
                self.assertEqual(replacement[2], PUSH_SERVER_MESSAGE)
                self.assertIn(socket.inet_aton("192.168.1.11"), replacement)
                self.assertIn(b"Second Host\x00", replacement)

            self.state.remove_server(second_source)
            for reader, _, cipher in browsers:
                removed = await self.read_browser_frame(reader, cipher)
                self.assertEqual(removed[2], DELETE_SERVER_MESSAGE)
                self.assertEqual(struct.unpack_from(">H", removed, 7)[0], 41002)

            self.state.report_server(first_source, b"HOST", first_keys)
            for reader, _, cipher in browsers:
                restored = await self.read_browser_frame(reader, cipher)
                self.assertEqual(restored[2], PUSH_SERVER_MESSAGE)
                self.assertIn(b"First Host\x00", restored)
        finally:
            for _, writer, _ in browsers:
                writer.close()
            await asyncio.gather(
                *(writer.wait_closed() for _, writer, _ in browsers),
                return_exceptions=True,
            )


    async def test_auto_fallback_relays_only_paired_opaque_datagrams(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(
                self.config.relay,
                fallback_seconds=0.1,
                packet_bytes=900,
                bytes_per_second=1024,
                byte_burst=1024,
            ),
        )
        first, second = await self.pair_nat_clients(b"RLY1")
        first_ping = await self.receive_udp_type(first, NN_CONNECT_PING)
        second_ping = await self.receive_udp_type(second, NN_CONNECT_PING)
        self.assertEqual(first_ping[18:20], b"\x01\x00")
        self.assertEqual(second_ping[18:20], b"\x01\x00")

        loop = asyncio.get_running_loop()
        await loop.sock_sendto(first, first_ping, ("127.0.0.1", self.nat_port))
        await loop.sock_sendto(second, second_ping, ("127.0.0.1", self.nat_port))
        await asyncio.sleep(0.01)

        payload = b"hbgs-opaque-relay"
        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            await loop.sock_sendto(first, payload, ("127.0.0.1", self.nat_port))
            forwarded, source = await asyncio.wait_for(
                loop.sock_recvfrom(second, 4096),
                1,
            )
        self.assertEqual(forwarded, payload)
        self.assertEqual(source[1], self.nat_port)
        self.assertIn("event=relay_forwarding", "\n".join(captured.output))

        intruder = self.udp_socket()
        await loop.sock_sendto(intruder, b"injected", ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(first, 4096), 0.05)

        intruder_init = (
            MAGIC
            + bytes((3, NN_INIT))
            + b"RLY1"
            + bytes((0, 0, 0))
            + b"\x00" * 6
        )
        with self.assertLogs("fruitspy.natneg", level="WARNING") as captured:
            await loop.sock_sendto(
                intruder,
                intruder_init,
                ("127.0.0.1", self.nat_port),
            )
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(loop.sock_recvfrom(intruder, 4096), 0.05)
        self.assertIn("event=peer_rejected", "\n".join(captured.output))
        self.assertIn("reason=relay_active", "\n".join(captured.output))

        await loop.sock_sendto(first, b"x" * 901, ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 0.05)

        await loop.sock_sendto(first, b"a" * 800, ("127.0.0.1", self.nat_port))
        forwarded, _ = await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 1)
        self.assertEqual(forwarded, b"a" * 800)
        await loop.sock_sendto(first, b"b" * 800, ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 0.05)

    async def test_active_relay_survives_nat_session_expiry(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(self.config.relay, fallback_seconds=0.05),
        )
        first, second = await self.pair_nat_clients(b"KEEP")
        first_ping = await self.receive_udp_type(first, NN_CONNECT_PING)
        second_ping = await self.receive_udp_type(second, NN_CONNECT_PING)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(first, first_ping, ("127.0.0.1", self.nat_port))
        await loop.sock_sendto(second, second_ping, ("127.0.0.1", self.nat_port))
        await asyncio.sleep(0)

        session = self.state.nat_sessions[b"KEEP"]
        session.last_seen -= 61
        await loop.sock_sendto(first, b"active", ("127.0.0.1", self.nat_port))
        forwarded, _ = await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 1)
        self.assertEqual(forwarded, b"active")
        self.state.expire()
        self.assertIn(b"KEEP", self.state.nat_sessions)

        session.last_seen -= 61
        self.state.expire()
        self.assertNotIn(b"KEEP", self.state.nat_sessions)
        self.assertIn(b"KEEP", self.nat_protocol._relays)
        await loop.sock_sendto(first, b"still-relayed", ("127.0.0.1", self.nat_port))
        forwarded, _ = await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 1)
        self.assertEqual(forwarded, b"still-relayed")

    async def test_direct_success_report_cancels_auto_fallback(self) -> None:
        first, _ = await self.pair_nat_clients(b"DIR1")
        report = (
            MAGIC
            + bytes((3, NN_REPORT))
            + b"DIR1"
            + bytes((0, 0, 1))
            + struct.pack("<II", 2, 2)
            + b"FruitNinjaand\x00".ljust(50, b"\x00")
        )
        acknowledgement = await self.exchange_udp(first, report, self.nat_port)
        self.assertEqual(acknowledgement[7], NN_REPORT_ACK)
        with self.assertRaises(TimeoutError):
            await self.receive_udp_type(first, NN_CONNECT_PING, 0.15)

    async def test_late_direct_report_closes_unready_fallback_relay(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(self.config.relay, fallback_seconds=0.05),
        )
        first, second = await self.pair_nat_clients(b"RACE")
        await self.receive_udp_type(first, NN_CONNECT_PING)
        await self.receive_udp_type(second, NN_CONNECT_PING)
        self.assertIn(b"RACE", self.nat_protocol._relays)
        self.assertEqual(self.nat_protocol._relays[b"RACE"].ready, set())
        report = (
            MAGIC
            + bytes((3, NN_REPORT))
            + b"RACE"
            + bytes((0, 0, 1))
            + struct.pack("<II", 2, 2)
            + b"FruitNinjaand\x00".ljust(50, b"\x00")
        )

        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            acknowledgement = await self.exchange_udp(first, report, self.nat_port)

        self.assertEqual(acknowledgement[7], NN_REPORT_ACK)
        self.assertNotIn(b"RACE", self.nat_protocol._relays)
        self.assertNotIn(first.getsockname(), self.nat_protocol._relay_by_address)
        self.assertNotIn(second.getsockname(), self.nat_protocol._relay_by_address)
        self.assertEqual(
            self.state.nat_sessions[b"RACE"].successful_reports,
            {0},
        )
        output = "\n".join(captured.output)
        self.assertIn("event=relay_race_resolved session=52414345 outcome=direct", output)
        self.assertIn("reason=direct_success_race", output)

    async def test_relay_readiness_wins_report_boundary_race(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(self.config.relay, fallback_seconds=0.05),
        )
        first, second = await self.pair_nat_clients(b"RAC2")
        first_ping = await self.receive_udp_type(first, NN_CONNECT_PING)
        await self.receive_udp_type(second, NN_CONNECT_PING)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(first, first_ping, ("127.0.0.1", self.nat_port))
        await asyncio.sleep(0)
        report = (
            MAGIC
            + bytes((3, NN_REPORT))
            + b"RAC2"
            + bytes((0, 0, 1))
            + struct.pack("<II", 2, 2)
            + b"FruitNinjaand\x00".ljust(50, b"\x00")
        )

        acknowledgement = await self.exchange_udp(first, report, self.nat_port)

        self.assertEqual(acknowledgement[7], NN_REPORT_ACK)
        relay = self.nat_protocol._relays[b"RAC2"]
        self.assertEqual(relay.ready, {0})
        self.assertEqual(relay.reports, {0})
        self.assertEqual(
            self.state.nat_sessions[b"RAC2"].successful_reports,
            set(),
        )

    async def test_direct_policy_never_allocates_fallback(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(self.config.relay, policy="direct"),
        )
        first, _ = await self.pair_nat_clients(b"DIR2")
        with self.assertRaises(TimeoutError):
            await self.receive_udp_type(first, NN_CONNECT_PING, 0.15)

    async def test_relay_has_hard_session_ttl(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(
                self.config.relay,
                fallback_seconds=0.1,
                session_seconds=0.1,
            ),
        )
        first, second = await self.pair_nat_clients(b"TTL1")
        first_ping = await self.receive_udp_type(first, NN_CONNECT_PING)
        second_ping = await self.receive_udp_type(second, NN_CONNECT_PING)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(first, first_ping, ("127.0.0.1", self.nat_port))
        await loop.sock_sendto(second, second_ping, ("127.0.0.1", self.nat_port))
        await asyncio.sleep(0.12)
        await loop.sock_sendto(first, b"expired", ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(second, 4096), 0.05)

    async def test_relay_session_capacity_is_global(self) -> None:
        self.nat_protocol.config = replace(
            self.config,
            relay=replace(
                self.config.relay,
                fallback_seconds=0.1,
                sessions=1,
            ),
        )
        first, second = await self.pair_nat_clients(b"CAP1")
        await self.receive_udp_type(first, NN_CONNECT_PING)
        await self.receive_udp_type(second, NN_CONNECT_PING)

        third, _ = await self.pair_nat_clients(b"CAP2")
        with self.assertRaises(TimeoutError):
            await self.receive_udp_type(third, NN_CONNECT_PING, 0.2)

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

    async def test_server_message_relays_to_observed_qr_endpoint(self) -> None:
        target = self.udp_socket()
        target_address = target.getsockname()
        self.state.report_server(
            target_address,
            b"RLY1",
            {
                "gamename": "FruitNinjaandam",
                "hostport": "6500",
                "localip0": "10.0.0.10",
                "localport": "6500",
            },
        )
        self.state.register_server(target_address)

        reader, writer = await asyncio.open_connection("127.0.0.1", self.browser_port)
        try:
            payload = b"\xfd\xfc\x1e\x66\x6a\xb2UNIF"
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
        second_writer = None
        third_writer = None
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

            first_writer.close()
            await first_writer.wait_closed()
            for _ in range(100):
                if service.admission.total == 0:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(service.admission.total, 0)

            _, third_writer = await asyncio.open_connection("127.0.0.1", port)
            await asyncio.sleep(0)
            self.assertEqual(service.admission.total, 1)
        finally:
            if second_writer is not None:
                second_writer.close()
                await second_writer.wait_closed()
            if third_writer is not None:
                third_writer.close()
                await third_writer.wait_closed()
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
            await asyncio.sleep(1.05)
            recovered = await self.exchange_udp(client, request, port)
            self.assertEqual(recovered, b"\xfe\xfd\x09" + b"\x00" * 8)
        finally:
            transport.close()


    async def test_global_udp_budget_is_shared_by_qr_and_natneg(self) -> None:
        admission = UdpAdmission(100, 100, 1, 1, 8, 60, 3, 10)
        self.qr_protocol.udp_admission = admission
        self.nat_protocol.udp_admission = admission
        loop = asyncio.get_running_loop()
        qr_client = self.udp_socket()
        nat_client = self.udp_socket()
        availability = b"\x09\x00\x00\x00\x00FruitNinjaand\x00"
        natify = MAGIC + bytes((3, NN_NATIFY_REQUEST)) + b"GLOB"

        response = await self.exchange_udp(qr_client, availability, self.qr_port)
        self.assertEqual(response, b"\xfe\xfd\x09" + b"\x00" * 8)
        await loop.sock_sendto(nat_client, natify, ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(nat_client, 2048), 0.1)

        await asyncio.sleep(1.05)
        response = await self.exchange_udp(nat_client, natify, self.nat_port)
        self.assertEqual(response[7], NN_ERT_TEST)

    async def test_source_ban_applies_across_udp_listeners(self) -> None:
        admission = UdpAdmission(10, 1, 100, 100, 8, 60, 2, 0.2)
        self.qr_protocol.udp_admission = admission
        self.nat_protocol.udp_admission = admission
        loop = asyncio.get_running_loop()
        qr_client = self.udp_socket()
        nat_client = self.udp_socket()
        availability = b"\x09\x00\x00\x00\x00FruitNinjaand\x00"
        natify = MAGIC + bytes((3, NN_NATIFY_REQUEST)) + b"BANN"

        await self.exchange_udp(qr_client, availability, self.qr_port)
        await loop.sock_sendto(qr_client, availability, ("127.0.0.1", self.qr_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(qr_client, 2048), 0.05)
        with self.assertLogs("fruitspy.natneg", level="WARNING") as captured:
            await loop.sock_sendto(
                nat_client,
                natify,
                ("127.0.0.1", self.nat_port),
            )
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(loop.sock_recvfrom(nat_client, 2048), 0.05)
        self.assertIn("reason=source_ban_started", "\n".join(captured.output))

        await asyncio.sleep(0.21)
        response = await self.exchange_udp(qr_client, availability, self.qr_port)
        self.assertEqual(response, b"\xfe\xfd\x09" + b"\x00" * 8)


    async def test_udp_listeners_recover_after_malformed_packets(self) -> None:
        loop = asyncio.get_running_loop()
        qr_client = self.udp_socket()
        await loop.sock_sendto(qr_client, b"\x03", ("127.0.0.1", self.qr_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(qr_client, 2048), 0.1)
        availability = await self.exchange_udp(
            qr_client,
            b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
            self.qr_port,
        )
        self.assertEqual(availability, b"\xfe\xfd\x09" + b"\x00" * 8)

        nat_client = self.udp_socket()
        invalid_init = (
            MAGIC
            + bytes((3, NN_INIT))
            + b"BAD1"
            + bytes((0, 2, 1))
            + b"\x00" * 6
        )
        await loop.sock_sendto(nat_client, invalid_init, ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(loop.sock_recvfrom(nat_client, 2048), 0.1)
        natify = MAGIC + bytes((3, NN_NATIFY_REQUEST)) + b"GOOD"
        response = await self.exchange_udp(nat_client, natify, self.nat_port)
        self.assertEqual(response[7], NN_ERT_TEST)

if __name__ == "__main__":
    unittest.main()
