from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
import time
import unittest
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from fruitspy.admission import (
    TokenBucket,
    UdpAdmission,
    UdpAdmissionDecision,
)
from fruitspy.config import ServerConfig, load_config
from fruitspy.crypto import PeerChatCipher
from fruitspy.natneg import NatNegProtocol
from fruitspy.peerchat import PeerChatServer
from fruitspy.server_browser import ServerBrowserServer
from fruitspy.state import ServerState
from tests.test_peerchat import EncryptedPeerClient

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.json"


def production_config() -> ServerConfig:
    return load_config(CONFIG_PATH)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class CountingDatagramTransport:
    def __init__(self) -> None:
        self.packets = 0
        self.bytes = 0
        self.last_destination: tuple[str, int] | None = None

    def sendto(self, data: bytes, destination: tuple[str, int]) -> None:
        self.packets += 1
        self.bytes += len(data)
        self.last_destination = destination


class QuietLoadTestCase:
    def disable_load_logging(self) -> None:
        self._logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)

    def restore_load_logging(self) -> None:
        logging.disable(self._logging_disable)


class ConfiguredConnectionLoadTests(QuietLoadTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.disable_load_logging()
        config = production_config()
        self.config = replace(
            config,
            timeouts=replace(
                config.timeouts,
                peerchat_handshake_seconds=120,
                server_browser_idle_seconds=120,
            ),
        )
        self.chat = PeerChatServer(self.config)
        self.chat_listener = await asyncio.start_server(self.chat.handle, "127.0.0.1", 0)
        self.chat_port = self.chat_listener.sockets[0].getsockname()[1]
        self.browser = ServerBrowserServer(
            self.config,
            ServerState(
                self.config.timeouts.reported_server_seconds,
                self.config.timeouts.nat_session_seconds,
                self.config.limits.reported_servers,
                self.config.limits.nat_sessions,
            ),
        )
        self.browser_listener = await asyncio.start_server(
            self.browser.handle,
            "127.0.0.1",
            0,
        )
        self.browser_port = self.browser_listener.sockets[0].getsockname()[1]
        self.writers: list[asyncio.StreamWriter] = []

    async def asyncTearDown(self) -> None:
        for writer in self.writers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in self.writers),
            return_exceptions=True,
        )
        self.chat_listener.close()
        self.browser_listener.close()
        await self.chat_listener.wait_closed()
        await self.browser_listener.wait_closed()
        self.restore_load_logging()

    async def _wait_until(self, predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(10):
            while not predicate():
                await asyncio.sleep(0.01)

    async def _open(
        self,
        port: int,
        source: str,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            port,
            local_addr=(source, 0),
        )
        self.writers.append(writer)
        return reader, writer

    async def _assert_rejected(self, port: int, source: str) -> None:
        reader, _writer = await self._open(port, source)
        try:
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b"")
        except ConnectionResetError:
            pass

    async def _saturate(
        self,
        *,
        service: PeerChatServer | ServerBrowserServer,
        port: int,
        total_limit: int,
        per_source_limit: int,
    ) -> None:
        first_source = "127.0.0.2"
        for _ in range(per_source_limit):
            await self._open(port, first_source)
        await self._wait_until(lambda: service.admission.total == per_source_limit)
        await self._assert_rejected(port, first_source)
        self.assertEqual(service.admission.total, per_source_limit)

        while service.admission.total < total_limit:
            current = service.admission.total
            source_index = current // per_source_limit
            source = f"127.0.0.{source_index + 2}"
            batch = min(per_source_limit, total_limit - current)
            expected = current + batch
            for _ in range(batch):
                await self._open(port, source)
            await self._wait_until(lambda expected=expected: service.admission.total >= expected)

        self.assertEqual(service.admission.total, total_limit)
        extra_source = f"127.0.0.{total_limit // per_source_limit + 3}"
        await self._assert_rejected(port, extra_source)
        self.assertEqual(service.admission.total, total_limit)

        held_writers = [writer for writer in self.writers if not writer.is_closing()]
        for writer in held_writers:
            writer.close()
        await asyncio.gather(
            *(writer.wait_closed() for writer in held_writers),
            return_exceptions=True,
        )
        await self._wait_until(lambda: service.admission.total == 0)
        _reader, recovery_writer = await self._open(port, first_source)
        await self._wait_until(lambda: service.admission.total == 1)
        recovery_writer.close()
        await recovery_writer.wait_closed()
        await self._wait_until(lambda: service.admission.total == 0)

    async def test_peerchat_connections_reach_per_source_and_global_limits(self) -> None:
        await self._saturate(
            service=self.chat,
            port=self.chat_port,
            total_limit=self.config.limits.peerchat_connections,
            per_source_limit=self.config.limits.connections_per_source,
        )

    async def test_server_browser_connections_reach_per_source_and_global_limits(self) -> None:
        await self._saturate(
            service=self.browser,
            port=self.browser_port,
            total_limit=self.config.limits.server_browser_connections,
            per_source_limit=self.config.limits.connections_per_source,
        )


class ConfiguredPeerChatStateLoadTests(QuietLoadTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.disable_load_logging()
        base = production_config()
        limits = replace(
            base.limits,
            peerchat_commands_per_second=65_535,
            peerchat_command_burst=65_535,
            peerchat_state_creations_per_second=65_535,
            peerchat_state_creation_burst=65_535,
        )
        self.config = replace(base, limits=limits)
        self.service = PeerChatServer(self.config)
        self.listener = await asyncio.start_server(self.service.handle, "127.0.0.1", 0)
        self.port = self.listener.sockets[0].getsockname()[1]
        self.clients: list[EncryptedPeerClient] = []

    async def asyncTearDown(self) -> None:
        await asyncio.gather(*(client.close() for client in self.clients), return_exceptions=True)
        self.listener.close()
        await self.listener.wait_closed()
        self.restore_load_logging()

    async def _connect_player(self, index: int) -> EncryptedPeerClient:
        source = f"127.0.0.{index // self.config.limits.connections_per_source + 2}"
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            self.port,
            local_addr=(source, 0),
        )
        writer.write(b"CRYPT des 1 FruitNinjaand\r\n")
        await writer.drain()
        response = (await reader.readline()).decode("ascii")
        fields = response.strip().split()
        self.assertGreaterEqual(len(fields), 5)
        client_key, server_key = fields[-2:]
        client = EncryptedPeerClient(
            reader,
            writer,
            PeerChatCipher(client_key, self.config.game.secret_key),
            PeerChatCipher(server_key, self.config.game.secret_key),
        )
        self.clients.append(client)
        nick = f"load{index:03d}"
        await client.send(f"NICK {nick}")
        await client.send(f"USER {nick} 0 * :{nick}")
        await client.read_until(f"376 {nick}")
        return client

    async def _fill_client_channels(
        self,
        client: EncryptedPeerClient,
        index: int,
    ) -> None:
        for channel_index in range(self.config.limits.peerchat_channels_per_client):
            await client.send(f"JOIN #load-{index:03d}-{channel_index:02d}")
        marker = f"rooms-{index}"
        await client.send(f"PING :{marker}")
        await client.read_until(f"PONG :{marker}")

    async def test_rooms_memberships_and_key_collections_reach_configured_limits(self) -> None:
        per_client = self.config.limits.peerchat_channels_per_client
        total_channels = self.config.limits.peerchat_channels
        client_count = total_channels // per_client
        self.assertEqual(client_count * per_client, total_channels)

        clients = [await self._connect_player(index) for index in range(client_count)]
        await self._fill_client_channels(clients[0], 0)
        await clients[0].send("JOIN #load-over-per-client")
        self.assertIn(
            "You have joined too many channels",
            await clients[0].read_until("You have joined too many channels"),
        )
        self.assertNotIn("#load-over-per-client", self.service.channels)
        await clients[0].send("PART #load-000-00 :release")
        await clients[0].read_until("PART #load-000-00")
        await clients[0].send("JOIN #load-over-per-client")
        await clients[0].read_until("End of NAMES list")
        self.assertEqual(len(self.service.clients["load000"].channels), per_client)
        self.assertIn("#load-over-per-client", self.service.channels)

        await asyncio.gather(
            *(
                self._fill_client_channels(client, index)
                for index, client in enumerate(clients[1:], 1)
            )
        )
        self.assertEqual(len(self.service.channels), total_channels)
        self.assertTrue(
            all(len(client.channels) == per_client for client in self.service.clients.values())
        )

        extra = await self._connect_player(client_count)
        await extra.send("JOIN #load-over-global")
        self.assertIn(
            "You have joined too many channels",
            await extra.read_until("You have joined too many channels"),
        )
        self.assertNotIn("#load-over-global", self.service.channels)
        self.assertEqual(len(self.service.channels), total_channels)
        await clients[0].send("PART #load-000-01 :release")
        await clients[0].read_until("PART #load-000-01")
        await extra.send("JOIN #load-over-global")
        await extra.read_until("End of NAMES list")
        self.assertEqual(len(self.service.channels), total_channels)
        self.assertIn("#load-over-global", self.service.channels)

        first = clients[0]
        first_state = self.service.clients["load000"]
        key_limit = self.config.limits.peerchat_keys_per_collection
        user_pairs = "".join(f"\\u{index}\\v" for index in range(key_limit - 1))
        await first.send(f"SETKEY :{user_pairs}")
        await first.send("PING :user-keys-full")
        await first.read_until("PONG :user-keys-full")
        self.assertEqual(len(first_state.user_keys), key_limit)
        before_user_keys = dict(first_state.user_keys)
        await first.send("SETKEY :\\overflow\\x")
        await first.read_until("SETKEY :Resource limit exceeded")
        self.assertEqual(first_state.user_keys, before_user_keys)

        channel = self.service.channels["#load-over-per-client"]
        channel_pairs = "".join(f"\\c{index}\\v" for index in range(key_limit))
        await first.send(f"SETCHANKEY {channel.name} :{channel_pairs}")
        await first.send("PING :channel-keys-full")
        await first.read_until("PONG :channel-keys-full")
        self.assertEqual(len(channel.keys), key_limit)
        before_channel_keys = dict(channel.keys)
        await first.send(f"SETCHANKEY {channel.name} :\\overflow\\x")
        await first.read_until("SETCHANKEY :Resource limit exceeded")
        self.assertEqual(channel.keys, before_channel_keys)

        client_pairs = "".join(f"\\p{index}\\v" for index in range(key_limit))
        await first.send(f"SETCKEY {channel.name} load000 :{client_pairs}")
        await first.send("PING :client-keys-full")
        await first.read_until("PONG :client-keys-full")
        self.assertEqual(len(channel.client_keys["load000"]), key_limit)
        before_client_keys = dict(channel.client_keys["load000"])
        await first.send(f"SETCKEY {channel.name} load000 :\\overflow\\x")
        await first.read_until("SETCKEY :Resource limit exceeded")
        self.assertEqual(channel.client_keys["load000"], before_client_keys)

        await asyncio.gather(*(client.close() for client in self.clients))
        async with asyncio.timeout(10):
            while self.service.admission.total:
                await asyncio.sleep(0.01)
        self.assertFalse(self.service.clients)
        self.assertFalse(self.service.channels)


class ConfiguredPacketAndStateLoadTests(QuietLoadTestCase, unittest.TestCase):
    def setUp(self) -> None:
        self.disable_load_logging()
        self.config = production_config()

    def tearDown(self) -> None:
        self.restore_load_logging()

    def _admission(self, clock: FakeClock) -> UdpAdmission:
        limits = self.config.limits
        return UdpAdmission(
            limits.udp_packets_per_second,
            limits.udp_burst,
            limits.udp_global_packets_per_second,
            limits.udp_global_burst,
            limits.udp_tracked_sources,
            self.config.timeouts.rate_limit_entry_seconds,
            limits.udp_source_violation_burst,
            self.config.timeouts.udp_source_ban_seconds,
            clock=clock,
        )

    def test_command_state_and_udp_budgets_reach_exact_boundaries(self) -> None:
        limits = self.config.limits
        clock = FakeClock()
        for rate, burst in (
            (limits.peerchat_commands_per_second, limits.peerchat_command_burst),
            (
                limits.peerchat_state_creations_per_second,
                limits.peerchat_state_creation_burst,
            ),
        ):
            bucket = TokenBucket(rate, burst, clock=clock)
            self.assertTrue(all(bucket.allow() for _ in range(burst)))
            self.assertFalse(bucket.allow())
            clock.now += 1 / rate
            self.assertTrue(bucket.allow())

        source_clock = FakeClock()
        source_admission = self._admission(source_clock)
        source = "192.0.2.1"
        self.assertTrue(
            all(
                source_admission.allow(source) is UdpAdmissionDecision.ALLOWED
                for _ in range(limits.udp_burst)
            )
        )
        for _ in range(limits.udp_source_violation_burst - 1):
            self.assertIs(
                source_admission.allow(source),
                UdpAdmissionDecision.SOURCE_RATE_LIMIT,
            )
        self.assertIs(
            source_admission.allow(source),
            UdpAdmissionDecision.SOURCE_BAN_STARTED,
        )
        self.assertIs(
            source_admission.allow(source),
            UdpAdmissionDecision.SOURCE_BANNED,
        )
        source_clock.now = self.config.timeouts.udp_source_ban_seconds
        self.assertIs(
            source_admission.allow(source),
            UdpAdmissionDecision.ALLOWED,
        )

        global_clock = FakeClock()
        global_admission = self._admission(global_clock)
        packets_per_source = limits.udp_global_burst // limits.udp_tracked_sources
        self.assertEqual(
            packets_per_source * limits.udp_tracked_sources,
            limits.udp_global_burst,
        )
        for source_index in range(limits.udp_tracked_sources):
            load_source = f"load-{source_index}"
            for _ in range(packets_per_source):
                self.assertIs(
                    global_admission.allow(load_source),
                    UdpAdmissionDecision.ALLOWED,
                )
        self.assertIs(
            global_admission.allow("load-0"),
            UdpAdmissionDecision.GLOBAL_RATE_LIMIT,
        )
        global_clock.now = 1 / limits.udp_global_packets_per_second
        self.assertIs(
            global_admission.allow("load-0"),
            UdpAdmissionDecision.ALLOWED,
        )

        source_table_clock = FakeClock()
        source_table = self._admission(source_table_clock)
        for source_index in range(limits.udp_tracked_sources):
            self.assertIs(
                source_table.allow(f"tracked-{source_index}"),
                UdpAdmissionDecision.ALLOWED,
            )
        self.assertEqual(len(source_table._sources), limits.udp_tracked_sources)
        self.assertIs(
            source_table.allow("tracked-overflow"),
            UdpAdmissionDecision.SOURCE_TABLE_FULL,
        )
        self.assertEqual(len(source_table._sources), limits.udp_tracked_sources)
        source_table_clock.now = self.config.timeouts.rate_limit_entry_seconds + 1
        self.assertIs(
            source_table.allow("tracked-after-expiry"),
            UdpAdmissionDecision.ALLOWED,
        )
        self.assertEqual(len(source_table._sources), 1)

    def test_reported_server_and_natneg_state_reach_exact_capacities(self) -> None:
        limits = self.config.limits
        state = ServerState(
            self.config.timeouts.reported_server_seconds,
            self.config.timeouts.nat_session_seconds,
            limits.reported_servers,
            limits.nat_sessions,
        )
        for index in range(limits.reported_servers):
            source = ("192.0.2.1", 20_000 + index)
            state.report_server(source, index.to_bytes(4, "big"), {"gamename": "FruitNinjaand"})
        self.assertEqual(len(state.reported_servers), limits.reported_servers)
        existing_server = ("192.0.2.1", 20_000)
        state.report_server(existing_server, b"UPDT", {"gamename": "FruitNinjaand"})
        with self.assertRaisesRegex(ValueError, "reported server capacity"):
            state.report_server(
                ("192.0.2.1", 20_000 + limits.reported_servers),
                b"OVER",
                {"gamename": "FruitNinjaand"},
            )
        self.assertEqual(len(state.reported_servers), limits.reported_servers)
        state.remove_server(existing_server)
        replacement_server = ("192.0.2.1", 20_000 + limits.reported_servers)
        state.report_server(
            replacement_server,
            b"REPL",
            {"gamename": "FruitNinjaand"},
        )
        self.assertEqual(len(state.reported_servers), limits.reported_servers)
        self.assertIn(replacement_server, state.reported_servers)

        for index in range(limits.nat_sessions):
            cookie = index.to_bytes(4, "big")
            state.claim_nat_peer(cookie, 0, ("198.51.100.1", 20_000 + index), 3)
        self.assertEqual(len(state.nat_sessions), limits.nat_sessions)
        state.claim_nat_peer(b"\x00\x00\x00\x00", 0, ("198.51.100.1", 20_000), 3)
        with self.assertRaisesRegex(ValueError, "NatNeg session capacity"):
            state.claim_nat_peer(
                limits.nat_sessions.to_bytes(4, "big"),
                0,
                ("198.51.100.1", 20_000 + limits.nat_sessions),
                3,
            )
        self.assertEqual(len(state.nat_sessions), limits.nat_sessions)
        expiring_cookie = b"\x00\x00\x00\x01"
        state.nat_sessions[expiring_cookie].last_seen -= (
            self.config.timeouts.nat_session_seconds + 1
        )
        replacement_cookie = limits.nat_sessions.to_bytes(4, "big")
        state.claim_nat_peer(
            replacement_cookie,
            0,
            ("198.51.100.1", 20_000 + limits.nat_sessions),
            3,
        )
        self.assertEqual(len(state.nat_sessions), limits.nat_sessions)
        self.assertNotIn(expiring_cookie, state.nat_sessions)
        self.assertIn(replacement_cookie, state.nat_sessions)


class ConfiguredRelayLoadTests(QuietLoadTestCase, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.disable_load_logging()

    async def asyncTearDown(self) -> None:
        self.restore_load_logging()

    async def test_relays_reach_session_and_per_endpoint_byte_limits(self) -> None:
        config = production_config()
        state = ServerState(
            config.timeouts.reported_server_seconds,
            config.timeouts.nat_session_seconds,
            config.limits.reported_servers,
            config.limits.nat_sessions,
        )
        protocol = NatNegProtocol(config, state)
        transport = CountingDatagramTransport()
        protocol.connection_made(transport)  # type: ignore[arg-type]
        base_address = int(ipaddress.IPv4Address("10.0.0.1"))

        cookies: list[bytes] = []
        for index in range(config.relay.sessions):
            cookie = index.to_bytes(4, "big")
            cookies.append(cookie)
            first = (str(ipaddress.IPv4Address(base_address + index * 2)), 20_000 + index)
            second = (str(ipaddress.IPv4Address(base_address + index * 2 + 1)), 40_000 + index)
            session, _ = state.claim_nat_peer(cookie, 0, first, 3)
            state.claim_nat_peer(cookie, 1, second, 3)
            session.paired_at = time.monotonic()
            protocol._activate_relay(cookie)

        self.assertEqual(protocol.active_relays, config.relay.sessions)
        self.assertEqual(len(protocol._relay_by_address), config.relay.sessions * 2)
        self.assertEqual(transport.packets, config.relay.sessions * 2)

        overflow_cookie = config.relay.sessions.to_bytes(4, "big")
        overflow_first = ("10.255.0.1", 30_000)
        overflow_second = ("10.255.0.2", 30_001)
        overflow_session, _ = state.claim_nat_peer(overflow_cookie, 0, overflow_first, 3)
        state.claim_nat_peer(overflow_cookie, 1, overflow_second, 3)
        overflow_session.paired_at = time.monotonic()
        protocol._activate_relay(overflow_cookie)
        self.assertNotIn(overflow_cookie, protocol._relays)
        self.assertEqual(protocol.active_relays, config.relay.sessions)

        first_cookie = cookies[0]
        first_relay = protocol._relays[first_cookie]
        first_endpoint = first_relay.endpoints[0].address
        second_endpoint = first_relay.endpoints[1].address
        ping = protocol._relay_ping_packet(3, first_cookie)
        now = time.monotonic()
        self.assertTrue(protocol._handle_relay_ping(ping, first_endpoint, now))
        self.assertTrue(protocol._handle_relay_ping(ping, second_endpoint, now))

        payload = b"x" * config.relay.packet_bytes
        packet_capacity = config.relay.byte_burst // len(payload)
        before_packets = transport.packets
        self.assertTrue(
            all(
                protocol._forward_relay(payload, first_endpoint, now)
                for _ in range(packet_capacity)
            )
        )
        self.assertEqual(transport.packets - before_packets, packet_capacity)
        self.assertTrue(protocol._forward_relay(payload, first_endpoint, now))
        self.assertEqual(transport.packets - before_packets, packet_capacity)
        self.assertEqual(transport.last_destination, second_endpoint)

        metrics = protocol.metrics.render().decode("utf-8")
        self.assertIn(
            f'fruitspy_natneg_relays {config.relay.sessions}',
            metrics,
        )
        self.assertIn(
            'fruitspy_relay_events_total{event="unavailable"} 1',
            metrics,
        )
        self.assertIn(
            'fruitspy_protocol_rejections_total{service="natneg",reason="capacity"} 1',
            metrics,
        )
        self.assertIn(
            'fruitspy_relay_packets_total{result="dropped"} 1',
            metrics,
        )
        protocol._finish_relay(first_cookie, "load_test_release")
        protocol._activate_relay(overflow_cookie)
        self.assertEqual(protocol.active_relays, config.relay.sessions)
        self.assertIn(overflow_cookie, protocol._relays)

        protocol.close_relays("load_test_cleanup")
        self.assertEqual(protocol.active_relays, 0)
        self.assertFalse(protocol._relay_by_address)
        self.assertFalse(protocol._relay_expirations)
        self.assertTrue(protocol._relays_empty.is_set())


if __name__ == "__main__":
    unittest.main()
