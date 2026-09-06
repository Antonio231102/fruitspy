import asyncio
import unittest

from fruitspy.metrics import MetricsHttpServer, MetricsRegistry
from fruitspy.state import ServerState


class MetricsRegistryTests(unittest.TestCase):
    def test_registry_renders_fixed_counters_gauges_and_histograms(self) -> None:
        metrics = MetricsRegistry()
        metrics.set_gauge("fruitspy_peerchat_clients", 2)
        metrics.increment("fruitspy_discovery_requests_total", kind="list")
        metrics.increment("fruitspy_natneg_outcomes_total", outcome="direct")
        metrics.increment(
            "fruitspy_admission_rejections_total",
            service="natneg",
            reason="source_rate_limit",
        )
        metrics.observe("fruitspy_natneg_setup_seconds", 0.2, path="direct")

        output = metrics.render().decode("utf-8")

        self.assertIn("fruitspy_peerchat_clients 2", output)
        self.assertIn(
            'fruitspy_discovery_requests_total{kind="list"} 1',
            output,
        )
        self.assertIn(
            'fruitspy_natneg_outcomes_total{outcome="direct"} 1',
            output,
        )
        self.assertIn(
            'fruitspy_admission_rejections_total'
            '{service="natneg",reason="source_rate_limit"} 1',
            output,
        )
        self.assertIn(
            'fruitspy_natneg_setup_seconds_bucket{path="direct",le="0.25"} 1',
            output,
        )
        self.assertIn('fruitspy_natneg_setup_seconds_count{path="direct"} 1', output)
        self.assertIn('fruitspy_natneg_setup_seconds_sum{path="direct"} 0.2', output)

    def test_registry_rejects_unbounded_or_unknown_labels(self) -> None:
        metrics = MetricsRegistry()

        with self.assertRaisesRegex(ValueError, "unsupported label value"):
            metrics.increment(
                "fruitspy_protocol_rejections_total",
                service="192.0.2.1",
                reason="malformed",
            )
        with self.assertRaisesRegex(ValueError, "invalid labels"):
            metrics.increment(
                "fruitspy_discovery_requests_total",
                kind="list",
                nickname="player",
            )

    def test_state_gauges_track_only_aggregate_counts(self) -> None:
        metrics = MetricsRegistry()
        state = ServerState(120, 60, metrics=metrics)
        source = ("192.0.2.1", 6500)
        state.report_server(
            source,
            b"PRIV",
            {"gamename": "FruitNinjaand", "hostname": "Private Host"},
        )
        state.register_server(source)
        state.claim_nat_peer(b"SECR", 0, ("192.0.2.2", 40000), 3)

        output = metrics.render().decode("utf-8")
        self.assertIn("fruitspy_qr_reported_servers 1", output)
        self.assertIn("fruitspy_qr_registered_servers 1", output)
        self.assertIn("fruitspy_natneg_sessions 1", output)
        self.assertNotIn("Private Host", output)
        self.assertNotIn("192.0.2.", output)


class MetricsHttpServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.metrics = MetricsRegistry()
        endpoint = MetricsHttpServer(self.metrics)
        self.server = await asyncio.start_server(
            endpoint.handle,
            "127.0.0.1",
            0,
            limit=4096,
        )
        socket = self.server.sockets[0]
        self.port = socket.getsockname()[1]

    async def asyncTearDown(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def request(self, target: str) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(
            f"GET {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii")
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 1)
        writer.close()
        await writer.wait_closed()
        return response

    async def test_metrics_endpoint_serves_prometheus_text(self) -> None:
        self.metrics.set_gauge("fruitspy_qr_registered_servers", 1)

        response = await self.request("/metrics")

        self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(
            b"Content-Type: text/plain; version=0.0.4; charset=utf-8",
            response,
        )
        self.assertIn(b"fruitspy_qr_registered_servers 1\n", response)
        self.assertIn(b"Cache-Control: no-store", response)

    async def test_metrics_endpoint_rejects_other_paths(self) -> None:
        response = await self.request("/private-client-data")

        self.assertTrue(response.startswith(b"HTTP/1.1 404 Not Found\r\n"))
        self.assertNotIn(b"fruitspy_", response)


if __name__ == "__main__":
    unittest.main()
