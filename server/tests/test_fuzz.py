from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
import unittest
from dataclasses import replace

from fruitspy.admission import create_udp_admission
from fruitspy.availability_qr import (
    QR_AMPLIFICATION_DENOMINATOR,
    QR_AMPLIFICATION_NUMERATOR,
    AvailabilityQRProtocol,
)
from fruitspy.natneg import (
    MAGIC,
    NATNEG_AMPLIFICATION_LIMIT,
    NN_ADDRESS_CHECK,
    NN_CONNECT_ACK,
    NN_INIT,
    NN_NATIFY_REQUEST,
    NN_PREINIT,
    NN_REPORT,
    NatNegProtocol,
)
from fruitspy.peerchat import PeerChatServer
from fruitspy.server_browser import ServerBrowserServer, _matches_filter
from fruitspy.state import ReportedServer, ServerState
from tests.fuzz_helpers import mutation_cases
from tests.helpers import test_config
from tests.test_peerchat import EncryptedPeerClient

FUZZ_CASES = int(os.environ.get("FRUITSPY_FUZZ_CASES", "512"))
FUZZ_SEED = int(os.environ.get("FRUITSPY_FUZZ_SEED", "17620176"), 0)
if FUZZ_CASES < 1:
    raise ValueError("FRUITSPY_FUZZ_CASES must be positive")


def _browser_request(
    *,
    challenge: bytes = b"FUZZTEST",
    filter_text: bytes = b"",
    fields: bytes = b"\\hostname\\gamemode",
) -> bytes:
    payload = bytearray((1, 3))
    payload.extend(struct.pack(">I", 1))
    payload.extend(b"FruitNinjaand\x00FruitNinjaand\x00")
    payload.extend(challenge)
    payload.extend(filter_text + b"\x00")
    payload.extend(fields + b"\x00")
    payload.extend(struct.pack(">I", 0))
    return struct.pack(">H", len(payload) + 3) + b"\x00" + payload


def _natneg_seeds() -> tuple[bytes, ...]:
    cookie = b"FUZZ"
    return (
        MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 0)) + b"\x00" * 6,
        MAGIC + bytes((3, NN_ADDRESS_CHECK)) + cookie,
        MAGIC + bytes((3, NN_NATIFY_REQUEST)) + cookie,
        MAGIC + bytes((3, NN_REPORT)) + cookie + bytes((0, 0, 1)) + b"\x00" * 8,
        MAGIC + bytes((3, NN_PREINIT)) + cookie + b"\x00\x00",
        MAGIC + bytes((3, NN_CONNECT_ACK)) + cookie,
        MAGIC,
        b"opaque relay candidate",
    )


class ParserFuzzTests(unittest.TestCase):
    def setUp(self) -> None:
        self._logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        logging.disable(self._logging_disable)

    def test_qr2_and_natneg_datagram_parsers(self) -> None:
        config = test_config()
        qr_state = ServerState(120, 60)
        qr = AvailabilityQRProtocol(config, qr_state)
        qr_seeds = (
            b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
            b"\x03FUZZgamename\x00FruitNinjaand\x00hostname\x00Fuzzer\x00\x00",
            b"\x01FUZZproof\x00",
            b"\x08FUZZ",
            b"\x00",
        )
        source = ("192.0.2.10", 32000)
        for index, data in enumerate(
            mutation_cases(
                qr_seeds,
                count=FUZZ_CASES,
                random_seed=FUZZ_SEED,
                max_size=config.limits.qr_packet_bytes + 1,
            )
        ):
            try:
                response = qr.handle_datagram(data, source)
            except (ValueError, UnicodeError):
                continue
            except Exception as error:  # pragma: no cover - failure reports the generated index
                self.fail(f"QR2 fuzz case {index} raised {error!r}")
            if response is not None:
                self.assertLessEqual(
                    len(response) * QR_AMPLIFICATION_DENOMINATOR,
                    len(data) * QR_AMPLIFICATION_NUMERATOR,
                    f"QR2 fuzz case {index} exceeded the response limit",
                )
        self.assertLessEqual(len(qr_state.reported_servers), 1)

        nat_state = ServerState(120, 60, max_nat_sessions=config.limits.nat_sessions)
        natneg = NatNegProtocol(config, nat_state)
        for index, data in enumerate(
            mutation_cases(
                _natneg_seeds(),
                count=FUZZ_CASES,
                random_seed=FUZZ_SEED ^ 0x4E4154,
                max_size=config.limits.natneg_packet_bytes + 1,
            )
        ):
            try:
                responses = natneg.handle_datagram(data, source)
            except (ValueError, OSError):
                continue
            except Exception as error:  # pragma: no cover - failure reports the generated index
                self.fail(f"NatNeg fuzz case {index} raised {error!r}")
            self.assertLessEqual(
                sum(len(response) for response, _ in responses),
                len(data) * NATNEG_AMPLIFICATION_LIMIT,
                f"NatNeg fuzz case {index} exceeded the response limit",
            )
        self.assertLessEqual(len(nat_state.nat_sessions), config.limits.nat_sessions)
        self.assertEqual(natneg.active_relays, 0)
        self.assertFalse(natneg._fallbacks)

    def test_server_browser_packets_and_filters(self) -> None:
        config = test_config()
        state = ServerState(120, 60)
        reported = state.report_server(
            ("192.0.2.20", 32001),
            b"HOST",
            {
                "gamename": config.game.name,
                "hostname": "Fuzz Host",
                "gamemode": "openstaging",
                "maxplayers": "2",
                "numplayers": "1",
            },
        )
        state.register_server(reported.source)
        browser = ServerBrowserServer(config, state)
        packet_seeds = (
            _browser_request(),
            _browser_request(filter_text=b"gamemode='openstaging'"),
            _browser_request(filter_text=b"(numplayers=1) AND (maxplayers=2)"),
            b"\x00\x09\x01" + socket.inet_aton(reported.source[0]) + struct.pack(">H", reported.source[1]),
            b"\x00\x0a\x02" + socket.inet_aton(reported.source[0]) + struct.pack(">H", reported.source[1]) + b"message",
            b"\x00\x03\xff",
        )
        for index, packet in enumerate(
            mutation_cases(
                packet_seeds,
                count=FUZZ_CASES,
                random_seed=FUZZ_SEED ^ 0x42524F57,
                max_size=config.limits.server_browser_frame_bytes,
            )
        ):
            try:
                response = browser.handle_request(packet, "192.0.2.30")
            except ValueError:
                continue
            except Exception as error:  # pragma: no cover - failure reports the generated index
                self.fail(f"Server Browser fuzz case {index} raised {error!r}")
            self.assertTrue(response is None or isinstance(response, bytes))

        original_keys = dict(reported.keys)
        filter_seeds = (
            b"gamemode='openstaging'",
            b"(numplayers=1) AND (maxplayers=2)",
            b"hostname='Fuzz Host'",
            b"((((((((((((((((",
            b"key='unterminated",
            b"x" * 256,
            b"\x00\xff\\'()= AND ",
        )
        for index, data in enumerate(
            mutation_cases(
                filter_seeds,
                count=FUZZ_CASES,
                random_seed=FUZZ_SEED ^ 0x46494C54,
                max_size=config.limits.server_browser_frame_bytes,
            )
        ):
            expression = data.decode("utf-8", "replace")
            try:
                result = _matches_filter(reported, expression)
            except Exception as error:  # pragma: no cover - failure reports the generated index
                self.fail(f"filter fuzz case {index} raised {error!r}")
            self.assertIsInstance(result, bool)
            self.assertEqual(reported.keys, original_keys)


class LiveProtocolFuzzTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        base = test_config()
        limits = replace(
            base.limits,
            peerchat_commands_per_second=65_535,
            peerchat_command_burst=65_535,
            peerchat_state_creations_per_second=65_535,
            peerchat_state_creation_burst=65_535,
            udp_packets_per_second=65_535,
            udp_burst=65_535,
            udp_global_packets_per_second=65_535,
            udp_global_burst=65_535,
        )
        self.config = replace(base, limits=limits)
        self.state = ServerState(
            120,
            60,
            max_reported_servers=limits.reported_servers,
            max_nat_sessions=limits.nat_sessions,
        )
        self.udp_admission = create_udp_admission(self.config)
        self.loop = asyncio.get_running_loop()
        self.loop_errors: list[dict[str, object]] = []
        self.previous_exception_handler = self.loop.get_exception_handler()
        self.loop.set_exception_handler(lambda _loop, context: self.loop_errors.append(context))
        self.sockets: list[socket.socket] = []

        self.qr_transport, self.qr = await self.loop.create_datagram_endpoint(
            lambda: AvailabilityQRProtocol(self.config, self.state, self.udp_admission),
            local_addr=("127.0.0.1", 0),
        )
        self.qr_port = self.qr_transport.get_extra_info("sockname")[1]
        self.nat_transport, self.natneg = await self.loop.create_datagram_endpoint(
            lambda: NatNegProtocol(self.config, self.state, self.udp_admission),
            local_addr=("127.0.0.1", 0),
        )
        self.nat_port = self.nat_transport.get_extra_info("sockname")[1]
        self.peerchat = PeerChatServer(self.config)
        self.chat_listener = await asyncio.start_server(self.peerchat.handle, "127.0.0.1", 0)
        self.chat_port = self.chat_listener.sockets[0].getsockname()[1]
        self.browser = ServerBrowserServer(self.config, self.state)
        self.browser_listener = await asyncio.start_server(self.browser.handle, "127.0.0.1", 0)
        self.browser_port = self.browser_listener.sockets[0].getsockname()[1]

    async def asyncTearDown(self) -> None:
        for sock in self.sockets:
            sock.close()
        self.chat_listener.close()
        self.browser_listener.close()
        self.qr_transport.close()
        self.nat_transport.close()
        await self.chat_listener.wait_closed()
        await self.browser_listener.wait_closed()
        await asyncio.sleep(0)
        self.loop.set_exception_handler(self.previous_exception_handler)
        logging.disable(self._logging_disable)

    def _udp_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        self.sockets.append(sock)
        return sock

    async def _assert_no_loop_errors(self) -> None:
        await asyncio.sleep(0)
        if self.loop_errors:
            details = "; ".join(
                str(context.get("exception") or context.get("message"))
                for context in self.loop_errors
            )
            self.fail(f"fuzzed listener raised an unhandled exception: {details}")

    async def test_udp_listeners_survive_mutation_campaign(self) -> None:
        network_cases = max(32, FUZZ_CASES // 8)
        qr_socket = self._udp_socket()
        nat_socket = self._udp_socket()
        for data in mutation_cases(
            (
                b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
                b"\x03FUZZgamename\x00FruitNinjaand\x00\x00",
                b"\x08FUZZ",
                b"\x00",
            ),
            count=network_cases,
            random_seed=FUZZ_SEED ^ 0x515232,
            max_size=self.config.limits.qr_packet_bytes + 1,
        ):
            await self.loop.sock_sendto(qr_socket, data, ("127.0.0.1", self.qr_port))
        for data in mutation_cases(
            _natneg_seeds(),
            count=network_cases,
            random_seed=FUZZ_SEED ^ 0x4E4554,
            max_size=self.config.limits.natneg_packet_bytes + 1,
        ):
            await self.loop.sock_sendto(nat_socket, data, ("127.0.0.1", self.nat_port))
        await asyncio.sleep(0.1)

        qr_probe = self._udp_socket()
        await self.loop.sock_sendto(
            qr_probe,
            b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
            ("127.0.0.1", self.qr_port),
        )
        qr_response, _ = await asyncio.wait_for(self.loop.sock_recvfrom(qr_probe, 2048), 2)
        self.assertEqual(qr_response, b"\xfe\xfd\x09" + b"\x00" * 8)

        nat_probe = self._udp_socket()
        await self.loop.sock_sendto(
            nat_probe,
            MAGIC + bytes((3, NN_NATIFY_REQUEST)) + b"LIVE",
            ("127.0.0.1", self.nat_port),
        )
        nat_response, _ = await asyncio.wait_for(self.loop.sock_recvfrom(nat_probe, 2048), 2)
        self.assertEqual(nat_response[7], 2)
        await self._assert_no_loop_errors()

    async def test_server_browser_listener_survives_mutation_campaign(self) -> None:
        network_cases = max(32, FUZZ_CASES // 8)
        seeds = (
            _browser_request(),
            b"\x00\x03\xff",
            b"\x00\x09\x01\x7f\x00\x00\x01\x19\x64",
            b"\x00\x0a\x02\x7f\x00\x00\x01\x19\x64x",
        )
        for data in mutation_cases(
            seeds,
            count=network_cases,
            random_seed=FUZZ_SEED ^ 0x544350,
            max_size=self.config.limits.server_browser_frame_bytes,
        ):
            packet = bytearray(data)
            if len(packet) < 3:
                packet.extend(b"\x00" * (3 - len(packet)))
            packet[:2] = struct.pack(">H", len(packet))
            _reader, writer = await asyncio.open_connection("127.0.0.1", self.browser_port)
            writer.write(packet)
            await writer.drain()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                writer.transport.abort()

        reader, writer = await asyncio.open_connection("127.0.0.1", self.browser_port)
        writer.write(_browser_request())
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), 2)
        self.assertTrue(response)
        writer.close()
        await writer.wait_closed()
        await self._assert_no_loop_errors()

    async def test_encrypted_peerchat_listener_survives_mutation_campaign(self) -> None:
        client = await EncryptedPeerClient.connect(self.chat_port)

        async def consume_responses() -> None:
            try:
                while await client.reader.read(4096):
                    pass
            except (ConnectionError, asyncio.CancelledError):
                pass

        response_task = asyncio.create_task(consume_responses())
        command_seeds = (
            b"PING :fuzz",
            b"NICK fuzz-player",
            b"USER fuzz 0 * :Fuzz Player",
            b"JOIN #GSP!fuzz",
            b"PART #GSP!fuzz :bye",
            b"MODE #GSP!fuzz +o nobody",
            b"PRIVMSG #GSP!fuzz :payload",
            b"SETKEY :\\b_flags\\ready",
            b"GETKEY nobody cookie :\\b_flags",
            b"SETCHANKEY #GSP!fuzz :\\b_flags\\ready",
            b"GETCHANKEY #GSP!fuzz cookie :\\b_flags",
            b"SETCKEY #GSP!fuzz nobody :\\b_flags\\ready",
            b"GETCKEY #GSP!fuzz * cookie 0 :\\b_flags",
            b"WHO #GSP!fuzz",
            b"NAMES #GSP!fuzz",
            b"TOPIC #GSP!fuzz :topic",
            b"LIST",
            b"USRIP",
            b"UNKNOWN command",
            b"PING :one\nPING :two",
        )
        try:
            for index, data in enumerate(
                mutation_cases(
                    command_seeds,
                    count=FUZZ_CASES,
                    random_seed=FUZZ_SEED ^ 0x495243,
                    max_size=min(512, self.config.limits.peerchat_line_bytes),
                )
            ):
                lines = []
                for line in data.replace(b"\r", b"").split(b"\n"):
                    name = line.lstrip().split(b" ", 1)[0].upper()
                    lines.append(b"FUZZ " + line if name in {b"CRYPT", b"QUIT"} else line)
                payload = b"\n".join(lines) + b"\r\n"
                client.writer.write(client.outgoing.transform(payload))
                if index % 32 == 31:
                    await client.writer.drain()
            await client.writer.drain()
            await asyncio.sleep(0.1)
        finally:
            client.writer.close()
            try:
                await asyncio.wait_for(client.writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                client.writer.transport.abort()
            response_task.cancel()
            await asyncio.gather(response_task, return_exceptions=True)

        async with asyncio.timeout(2):
            while self.peerchat.admission.total:
                await asyncio.sleep(0.01)
        self.assertFalse(self.peerchat.clients)
        self.assertFalse(self.peerchat.channels)

        oversized = await EncryptedPeerClient.connect(self.chat_port)
        oversized.writer.write(
            oversized.outgoing.transform(
                b"A" * (self.config.limits.peerchat_line_bytes + 1) + b"\r\n"
            )
        )
        await oversized.writer.drain()
        self.assertEqual(await asyncio.wait_for(oversized.reader.read(1), 2), b"")
        await oversized.close()

        probe = await EncryptedPeerClient.connect(self.chat_port)
        try:
            await probe.send("PING :healthy")
            self.assertIn("PONG :healthy", await probe.read_until("PONG :healthy"))
        finally:
            await probe.close()
        await self._assert_no_loop_errors()


if __name__ == "__main__":
    unittest.main()
