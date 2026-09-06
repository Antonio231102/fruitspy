import asyncio
import struct
import unittest
from dataclasses import replace
from unittest import mock

from fruitspy.availability_qr import AvailabilityQRProtocol
from fruitspy.drain import DrainController
from fruitspy.metrics import MetricsRegistry
from fruitspy.natneg import MAGIC, NN_CONNECT, NN_INIT, NatNegProtocol
from fruitspy.server_browser import ServerBrowserServer
from fruitspy.state import ServerState
from tests.helpers import test_config


def natneg_init(cookie: bytes, client_index: int) -> bytes:
    return (
        MAGIC
        + bytes((3, NN_INIT))
        + cookie
        + bytes((0, client_index, 0))
        + b"\x00" * 6
    )


class DrainControllerTests(unittest.TestCase):
    def test_request_and_completion_are_idempotent_and_observable(self) -> None:
        metrics = MetricsRegistry()
        drain = DrainController(metrics)

        self.assertTrue(drain.request())
        self.assertFalse(drain.request())
        drain.complete(timed_out=False)
        drain.complete(timed_out=True)

        output = metrics.render().decode("utf-8")
        self.assertIn("fruitspy_server_draining 1", output)
        self.assertIn('fruitspy_drain_events_total{event="started"} 1', output)
        self.assertIn('fruitspy_drain_events_total{event="completed"} 1', output)
        self.assertIn('fruitspy_drain_events_total{event="timed_out"} 0', output)

    def test_qr_reports_unavailable_and_refuses_registration_while_draining(
        self,
    ) -> None:
        metrics = MetricsRegistry()
        drain = DrainController(metrics)
        state = ServerState(120, 60, metrics=metrics)
        protocol = AvailabilityQRProtocol(
            test_config(),
            state,
            metrics=metrics,
            drain=drain,
        )
        drain.request()
        source = ("192.0.2.1", 6500)

        availability = protocol.handle_datagram(
            b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
            source,
        )
        heartbeat = protocol.handle_datagram(
            b"\x03HOSTgamename\x00FruitNinjaand\x00",
            source,
        )

        self.assertIsNotNone(availability)
        assert availability is not None
        self.assertEqual(struct.unpack(">I", availability[-4:])[0], 1)
        self.assertIsNone(heartbeat)
        self.assertEqual(state.reported_servers, {})
        output = metrics.render().decode("utf-8")
        self.assertIn(
            'fruitspy_protocol_rejections_total{service="qr",reason="draining"} 2',
            output,
        )

    def test_natneg_finishes_existing_setup_but_refuses_new_sessions(
        self,
    ) -> None:
        metrics = MetricsRegistry()
        drain = DrainController(metrics)
        state = ServerState(120, 60, metrics=metrics)
        protocol = NatNegProtocol(
            test_config(),
            state,
            metrics=metrics,
            drain=drain,
        )
        cookie = b"KEEP"
        protocol.handle_datagram(
            natneg_init(cookie, 0),
            ("192.0.2.1", 40000),
        )
        drain.request()

        responses = protocol.handle_datagram(
            natneg_init(cookie, 1),
            ("192.0.2.2", 40001),
        )
        rejected = protocol.handle_datagram(
            natneg_init(b"DROP", 0),
            ("192.0.2.3", 40002),
        )

        self.assertEqual(sum(packet[7] == NN_CONNECT for packet, _ in responses), 2)
        self.assertEqual(rejected, [])
        self.assertNotIn(b"DROP", state.nat_sessions)
        self.assertEqual(protocol.begin_drain(), 0)
        protocol._activate_relay(cookie)
        self.assertEqual(protocol.active_relays, 0)
        output = metrics.render().decode("utf-8")
        self.assertIn(
            'fruitspy_protocol_rejections_total'
            '{service="natneg",reason="draining"} 2',
            output,
        )


class NatNegDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_begin_drain_cancels_pending_relay_fallback(self) -> None:
        metrics = MetricsRegistry()
        drain = DrainController(metrics)
        state = ServerState(120, 60, metrics=metrics)
        config = test_config()
        config = replace(
            config,
            relay=replace(config.relay, fallback_seconds=0.05),
        )
        protocol = NatNegProtocol(
            config,
            state,
            metrics=metrics,
            drain=drain,
        )
        protocol.transport = mock.Mock()
        cookie = b"WAIT"
        protocol.handle_datagram(
            natneg_init(cookie, 0),
            ("192.0.2.1", 40000),
        )
        protocol.handle_datagram(
            natneg_init(cookie, 1),
            ("192.0.2.2", 40001),
        )
        self.assertIn(cookie, protocol._fallbacks)

        drain.request()
        self.assertEqual(protocol.begin_drain(), 0)
        await asyncio.sleep(0.06)

        self.assertEqual(protocol._fallbacks, {})
        self.assertEqual(protocol.active_relays, 0)


class ServerBrowserDrainTests(unittest.IsolatedAsyncioTestCase):
    async def test_drain_closes_existing_and_rejects_new_connections(self) -> None:
        metrics = MetricsRegistry()
        drain = DrainController(metrics)
        state = ServerState(120, 60, metrics=metrics)
        service = ServerBrowserServer(
            test_config(),
            state,
            metrics,
            drain,
        )
        listener = await asyncio.start_server(
            service.handle,
            "127.0.0.1",
            0,
        )
        port = listener.sockets[0].getsockname()[1]
        try:
            existing_reader, existing_writer = await asyncio.open_connection(
                "127.0.0.1",
                port,
            )
            await asyncio.sleep(0)
            drain.request()
            await service.begin_drain()
            self.assertEqual(await asyncio.wait_for(existing_reader.read(), 1), b"")

            new_reader, new_writer = await asyncio.open_connection(
                "127.0.0.1",
                port,
            )
            self.assertEqual(await asyncio.wait_for(new_reader.read(), 1), b"")
            new_writer.close()
            await new_writer.wait_closed()
            await asyncio.sleep(0)
            output = metrics.render().decode("utf-8")
            self.assertIn("fruitspy_server_browser_connections 0", output)
            self.assertIn(
                'fruitspy_protocol_rejections_total'
                '{service="server_browser",reason="draining"} 1',
                output,
            )
            existing_writer.close()
        finally:
            listener.close()
            await listener.wait_closed()


if __name__ == "__main__":
    unittest.main()
