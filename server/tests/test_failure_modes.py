from __future__ import annotations

import asyncio
import json
import socket
import struct
import sys
import tempfile
import time
from collections.abc import Callable
import unittest
from dataclasses import replace
from pathlib import Path

from fruitspy.admission import create_udp_admission
from fruitspy.availability_qr import AvailabilityQRProtocol
from fruitspy.config import ServerConfig, load_config
from fruitspy.crypto import gsseckey
from fruitspy.healthcheck import wait_for_server
from fruitspy.natneg import (
    MAGIC,
    NN_CONNECT,
    NN_CONNECT_PING,
    NN_INIT,
    NN_INIT_ACK,
    NN_REPORT,
    NN_REPORT_ACK,
    NatNegProtocol,
)
from fruitspy.peerchat import PeerChatServer
from fruitspy.server_browser import PUSH_UPDATES, ServerBrowserServer
from fruitspy.state import ServerState
from tests.fault_helpers import DatagramDelivery, DatagramFaultPlan
from tests.helpers import test_config
from tests.test_peerchat import EncryptedPeerClient
from tests.test_protocols import decrypt_server_browser, server_browser_request

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"
Address = tuple[str, int]


class NetworkFailureModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        base = test_config()
        self.config = replace(
            base,
            relay=replace(base.relay, fallback_seconds=0.05, session_seconds=10),
        )
        self.state = ServerState(120, 60)
        self.loop = asyncio.get_running_loop()
        self.udp_admission = create_udp_admission(self.config)
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
        self.sockets: list[socket.socket] = []
        self.chat_clients: list[EncryptedPeerClient] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(
            *(client.close() for client in self.chat_clients),
            return_exceptions=True,
        )
        for sock in self.sockets:
            sock.close()
        self.chat_listener.close()
        self.browser_listener.close()
        self.nat_transport.close()
        await self.chat_listener.wait_closed()
        await self.browser_listener.wait_closed()

    def udp_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        self.sockets.append(sock)
        return sock

    async def receive_type(
        self,
        sock: socket.socket,
        packet_type: int,
        timeout: float = 2,
    ) -> bytes:
        deadline = self.loop.time() + timeout
        while True:
            packet, _ = await asyncio.wait_for(
                self.loop.sock_recvfrom(sock, 4096),
                deadline - self.loop.time(),
            )
            if len(packet) > 7 and packet[7] == packet_type:
                return packet

    async def pair_nat_clients(
        self,
        cookie: bytes,
    ) -> tuple[socket.socket, socket.socket]:
        first = self.udp_socket()
        second = self.udp_socket()
        first_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 0)) + b"\x00" * 6
        second_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 0)) + b"\x00" * 6
        await self.loop.sock_sendto(first, first_init, ("127.0.0.1", self.nat_port))
        self.assertEqual((await self.receive_type(first, NN_INIT_ACK))[7], NN_INIT_ACK)
        await self.loop.sock_sendto(second, second_init, ("127.0.0.1", self.nat_port))
        await self.receive_type(first, NN_CONNECT)
        await self.receive_type(second, NN_INIT_ACK)
        await self.receive_type(second, NN_CONNECT)
        return first, second

    async def establish_relay(
        self,
        cookie: bytes,
    ) -> tuple[socket.socket, socket.socket]:
        first, second = await self.pair_nat_clients(cookie)
        first_ping = await self.receive_type(first, NN_CONNECT_PING)
        second_ping = await self.receive_type(second, NN_CONNECT_PING)
        await self.loop.sock_sendto(first, first_ping, ("127.0.0.1", self.nat_port))
        await self.loop.sock_sendto(second, second_ping, ("127.0.0.1", self.nat_port))
        await asyncio.sleep(0)
        await self.wait_until(lambda: self.natneg._relays[cookie].ready == {0, 1})
        return first, second

    async def connect_player(self, nick: str) -> EncryptedPeerClient:
        client = await EncryptedPeerClient.connect(self.chat_port)
        self.chat_clients.append(client)
        await client.send(f"NICK {nick}")
        await client.send(f"USER {nick} 0 * :{nick}")
        await client.read_until(f"376 {nick}")
        return client

    async def wait_until(self, predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0.01)

    async def test_natneg_recovers_from_loss_reordering_duplicates_and_delay(self) -> None:
        cookie = b"FLTS"
        first = self.udp_socket()
        second = self.udp_socket()
        packets = (
            MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 0)) + b"\x00" * 6,
            MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 0)) + b"\x00" * 6,
        )
        plan = DatagramFaultPlan(
            (
                DatagramDelivery(1),
                DatagramDelivery(1, 0.01),
            )
        )

        await plan.transmit(
            self.loop,
            second,
            ("127.0.0.1", self.nat_port),
            packets,
        )
        second_types = [
            (await asyncio.wait_for(self.loop.sock_recvfrom(second, 4096), 2))[0][7]
            for _ in range(2)
        ]
        self.assertEqual(second_types, [NN_INIT_ACK, NN_INIT_ACK])

        await self.loop.sock_sendto(first, packets[0], ("127.0.0.1", self.nat_port))
        first_types = {
            (await asyncio.wait_for(self.loop.sock_recvfrom(first, 4096), 2))[0][7]
            for _ in range(2)
        }
        self.assertEqual(first_types, {NN_INIT_ACK, NN_CONNECT})
        self.assertEqual((await self.receive_type(second, NN_CONNECT))[7], NN_CONNECT)
        session = self.state.nat_sessions[cookie]
        self.assertEqual(set(session.peers), {0, 1})
        self.assertEqual(session.peers[0].address, first.getsockname())
        self.assertEqual(session.peers[1].address, second.getsockname())

        await self.loop.sock_sendto(first, packets[0], ("127.0.0.1", self.nat_port))
        await self.receive_type(first, NN_INIT_ACK)
        await self.receive_type(first, NN_CONNECT)
        await self.receive_type(second, NN_CONNECT)
        self.assertEqual(len(session.peers), 2)

        report = (
            MAGIC
            + bytes((3, NN_REPORT))
            + cookie
            + bytes((0, 0, 1))
            + struct.pack("<II", 2, 2)
        )
        await self.loop.sock_sendto(first, report, ("127.0.0.1", self.nat_port))
        await self.receive_type(first, NN_REPORT_ACK)
        await self.loop.sock_sendto(first, report, ("127.0.0.1", self.nat_port))
        await self.receive_type(first, NN_REPORT_ACK)
        await asyncio.sleep(self.config.relay.fallback_seconds + 0.03)
        self.assertNotIn(cookie, self.natneg._relays)
        self.assertEqual(session.successful_reports, {0})

    async def test_relay_survives_loss_reordering_duplicates_and_delay(self) -> None:
        first, second = await self.establish_relay(b"RFLT")
        logical_packets = (b"sequence-0", b"sequence-1", b"sequence-2")
        plan = DatagramFaultPlan(
            (
                DatagramDelivery(2),
                DatagramDelivery(0, 0.02),
                DatagramDelivery(0, 0.01),
            )
        )
        await plan.transmit(
            self.loop,
            first,
            ("127.0.0.1", self.nat_port),
            logical_packets,
        )
        received = [
            (await asyncio.wait_for(self.loop.sock_recvfrom(second, 4096), 2))[0]
            for _ in range(3)
        ]
        self.assertEqual(received, [b"sequence-2", b"sequence-0", b"sequence-0"])
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.loop.sock_recvfrom(second, 4096), 0.05)

        await self.loop.sock_sendto(second, b"reply", ("127.0.0.1", self.nat_port))
        reply, _ = await asyncio.wait_for(self.loop.sock_recvfrom(first, 4096), 2)
        self.assertEqual(reply, b"reply")
        self.assertEqual(self.natneg._relays[b"RFLT"].packets, 4)

    async def test_abrupt_tcp_death_releases_state_and_allows_reconnect(self) -> None:
        first = await self.connect_player("crash")
        second = await self.connect_player("survivor")
        await first.send("JOIN #failure")
        await first.read_until("End of NAMES list")
        await second.send("JOIN #failure")
        await second.read_until("End of NAMES list")
        await first.read_until("survivor!survivor@")

        first.writer.transport.abort()
        quit_message = await second.read_until("QUIT :Client exited")
        self.assertIn("crash!crash@", quit_message)
        await self.wait_until(lambda: "crash" not in self.peerchat.clients)
        self.assertEqual(set(self.peerchat.channels["#failure"].users), {"survivor"})

        replacement = await self.connect_player("crash")
        await replacement.send("JOIN #failure")
        await replacement.read_until("End of NAMES list")
        await second.read_until("crash!crash@")
        self.assertEqual(
            set(self.peerchat.channels["#failure"].users),
            {"crash", "survivor"},
        )

        browser_reader, browser_writer = await asyncio.open_connection(
            "127.0.0.1",
            self.browser_port,
        )
        browser_writer.write(server_browser_request(b"FAILMODE", "", options=PUSH_UPDATES))
        await browser_writer.drain()
        self.assertTrue(await asyncio.wait_for(browser_reader.read(4096), 2))
        self.assertEqual(len(self.state._server_change_events), 1)
        browser_writer.transport.abort()
        browser_writer.close()
        await asyncio.gather(browser_writer.wait_closed(), return_exceptions=True)
        await self.wait_until(lambda: self.browser.admission.total == 0)
        self.assertFalse(self.state._server_change_events)

        recovery_reader, recovery_writer = await asyncio.open_connection(
            "127.0.0.1",
            self.browser_port,
        )
        recovery_writer.write(server_browser_request(b"RECOVER!", ""))
        await recovery_writer.drain()
        self.assertTrue(await asyncio.wait_for(recovery_reader.read(4096), 2))
        recovery_writer.close()
        await recovery_writer.wait_closed()

    async def test_isolated_relay_state_loss_does_not_cross_wire_sessions(self) -> None:
        first_a, second_a = await self.establish_relay(b"LOSA")
        first_b, second_b = await self.establish_relay(b"LOSB")
        await self.loop.sock_sendto(first_a, b"before-a", ("127.0.0.1", self.nat_port))
        self.assertEqual(
            (await asyncio.wait_for(self.loop.sock_recvfrom(second_a, 4096), 2))[0],
            b"before-a",
        )
        await self.loop.sock_sendto(first_b, b"before-b", ("127.0.0.1", self.nat_port))
        self.assertEqual(
            (await asyncio.wait_for(self.loop.sock_recvfrom(second_b, 4096), 2))[0],
            b"before-b",
        )

        lost_endpoints = set(self.natneg._relays[b"LOSA"].endpoints[index].address for index in (0, 1))
        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            self.natneg._finish_relay(b"LOSA", "fault_injected")
        self.assertNotIn(b"LOSA", self.natneg._relays)
        self.assertTrue(lost_endpoints.isdisjoint(self.natneg._relay_by_address))
        self.assertIn("reason=fault_injected", "\n".join(captured.output))

        await self.loop.sock_sendto(first_a, b"after-loss", ("127.0.0.1", self.nat_port))
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.loop.sock_recvfrom(second_a, 4096), 0.05)

        await self.loop.sock_sendto(first_b, b"after-b", ("127.0.0.1", self.nat_port))
        self.assertEqual(
            (await asyncio.wait_for(self.loop.sock_recvfrom(second_b, 4096), 2))[0],
            b"after-b",
        )
        self.assertEqual(set(self.natneg._relays), {b"LOSB"})
        self.assertEqual(self.natneg._relays[b"LOSB"].packets, 2)


class ProcessRestartFailureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw["bind_host"] = "127.0.0.1"
        used_ports: set[int] = set()
        raw["ports"] = {
            "availability_qr_udp": self._free_port(socket.SOCK_DGRAM, used_ports),
            "peerchat_tcp": self._free_port(socket.SOCK_STREAM, used_ports),
            "server_browser_tcp": self._free_port(socket.SOCK_STREAM, used_ports),
            "natneg_udp": self._free_port(socket.SOCK_DGRAM, used_ports),
        }
        raw["metrics"] = {
            "bind_host": "127.0.0.1",
            "port": self._free_port(socket.SOCK_STREAM, used_ports),
        }
        raw["timeouts"]["drain_seconds"] = 1
        self.config_path = Path(self.temporary_directory.name) / "restart-config.json"
        self.config_path.write_text(json.dumps(raw), encoding="utf-8")
        self.config = load_config(self.config_path)
        self.processes: list[asyncio.subprocess.Process] = []
        self.sockets: list[socket.socket] = []

    async def asyncTearDown(self) -> None:
        for process in self.processes:
            if process.returncode is None:
                process.kill()
                await process.wait()
        for sock in self.sockets:
            sock.close()
        self.temporary_directory.cleanup()

    @staticmethod
    def _free_port(sock_type: int, used_ports: set[int]) -> int:
        while True:
            with socket.socket(socket.AF_INET, sock_type) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            if port not in used_ports:
                used_ports.add(port)
                return port

    async def start_process(self) -> asyncio.subprocess.Process:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "fruitspy",
            "--config",
            str(self.config_path),
            cwd=str(CONFIG_PATH.parent),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.processes.append(process)
        results = await asyncio.to_thread(
            wait_for_server,
            self.config,
            "127.0.0.1",
            0.2,
            10,
        )
        if not all(result.healthy for result in results):
            process.kill()
            await process.wait()
            self.fail(f"restarted process did not become healthy: {results}")
        return process

    def udp_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        sock.setblocking(False)
        self.sockets.append(sock)
        return sock

    async def exchange_udp(self, sock: socket.socket, data: bytes, port: int) -> bytes:
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(sock, data, ("127.0.0.1", port))
        response, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), 2)
        return response

    async def browser_query(self, challenge: bytes) -> bytes:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            self.config.ports.server_browser_tcp,
        )
        writer.write(server_browser_request(challenge, "\\hostname"))
        await writer.drain()
        encrypted = await asyncio.wait_for(reader.read(4096), 2)
        writer.close()
        await writer.wait_closed()
        return decrypt_server_browser(encrypted, challenge)

    async def assert_connection_closed(self, reader: asyncio.StreamReader) -> None:
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b"")
        except (ConnectionError, OSError):
            pass

    async def test_abrupt_process_restart_clears_state_and_restores_all_listeners(self) -> None:
        first_process = await self.start_process()
        qr_socket = self.udp_socket()
        instance = b"RSTR"
        heartbeat = (
            b"\x03"
            + instance
            + b"gamename\x00FruitNinjaand\x00"
            + b"hostname\x00Restart Host\x00"
            + b"hostport\x006500\x00"
            + b"maxplayers\x002\x00"
            + b"numplayers\x001\x00"
            + b"gamemode\x00openstaging\x00\x00"
        )
        challenge_packet = await self.exchange_udp(
            qr_socket,
            heartbeat,
            self.config.ports.availability_qr_udp,
        )
        challenge = challenge_packet[7:-1].decode("ascii")
        proof = gsseckey(challenge, self.config.game.secret_key)
        await self.exchange_udp(
            qr_socket,
            b"\x01" + instance + proof.encode("ascii") + b"\x00",
            self.config.ports.availability_qr_udp,
        )
        self.assertIn(b"Restart Host", await self.browser_query(b"BEFORE!!"))

        nat_socket = self.udp_socket()
        cookie = b"RSTR"
        first_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 0)) + b"\x00" * 6
        self.assertEqual(
            (await self.exchange_udp(nat_socket, first_init, self.config.ports.natneg_udp))[7],
            NN_INIT_ACK,
        )
        chat_reader, chat_writer = await asyncio.open_connection(
            "127.0.0.1",
            self.config.ports.peerchat_tcp,
        )
        browser_reader, browser_writer = await asyncio.open_connection(
            "127.0.0.1",
            self.config.ports.server_browser_tcp,
        )

        first_process.kill()
        await asyncio.wait_for(first_process.wait(), 5)
        await self.assert_connection_closed(chat_reader)
        await self.assert_connection_closed(browser_reader)
        chat_writer.close()
        browser_writer.close()
        await asyncio.gather(
            chat_writer.wait_closed(),
            browser_writer.wait_closed(),
            return_exceptions=True,
        )

        second_process = await self.start_process()
        self.assertNotIn(b"Restart Host", await self.browser_query(b"AFTER!!!"))
        second_nat_socket = self.udp_socket()
        second_init = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 0)) + b"\x00" * 6
        response = await self.exchange_udp(
            second_nat_socket,
            second_init,
            self.config.ports.natneg_udp,
        )
        self.assertEqual(response[7], NN_INIT_ACK)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                asyncio.get_running_loop().sock_recvfrom(second_nat_socket, 4096),
                0.1,
            )

        second_process.terminate()
        await asyncio.wait_for(second_process.wait(), 5)


if __name__ == "__main__":
    unittest.main()
