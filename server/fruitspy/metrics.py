from __future__ import annotations

import asyncio
from dataclasses import dataclass
from itertools import product


@dataclass(frozen=True, slots=True)
class _MetricSpec:
    name: str
    help: str
    kind: str
    labels: tuple[tuple[str, tuple[str, ...]], ...] = ()
    buckets: tuple[float, ...] = ()


@dataclass(slots=True)
class _HistogramValue:
    buckets: list[int]
    count: int = 0
    total: float = 0.0


_METRICS = (
    _MetricSpec(
        "fruitspy_server_draining",
        "Whether the process is draining and refusing new matchmaking.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_drain_events_total",
        "Graceful drain lifecycle events.",
        "counter",
        (("event", ("started", "completed", "timed_out")),),
    ),
    _MetricSpec(
        "fruitspy_peerchat_clients",
        "Current admitted PeerChat connections.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_peerchat_rooms",
        "Current PeerChat rooms.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_qr_reported_servers",
        "Current QR2 server records, including pending challenges.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_qr_registered_servers",
        "Current registered QR2 servers.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_server_browser_connections",
        "Current admitted Server Browser connections.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_natneg_sessions",
        "Current NatNeg setup sessions.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_natneg_relays",
        "Current gameplay relay allocations.",
        "gauge",
    ),
    _MetricSpec(
        "fruitspy_discovery_requests_total",
        "Server Browser requests by fixed request kind.",
        "counter",
        (("kind", ("list", "info", "relay")),),
    ),
    _MetricSpec(
        "fruitspy_discovery_results_total",
        "Server Browser list results by empty or nonempty result.",
        "counter",
        (("result", ("empty", "nonempty")),),
    ),
    _MetricSpec(
        "fruitspy_natneg_outcomes_total",
        "Established NatNeg paths by direct or relay outcome.",
        "counter",
        (("outcome", ("direct", "relay")),),
    ),
    _MetricSpec(
        "fruitspy_natneg_reports_total",
        "Validated NatNeg client reports by result.",
        "counter",
        (("result", ("success", "failure")),),
    ),
    _MetricSpec(
        "fruitspy_relay_events_total",
        "Gameplay relay lifecycle events.",
        "counter",
        (("event", ("activated", "established", "closed", "unavailable")),),
    ),
    _MetricSpec(
        "fruitspy_relay_packets_total",
        "Gameplay relay packets by forwarding result.",
        "counter",
        (("result", ("forwarded", "dropped")),),
    ),
    _MetricSpec(
        "fruitspy_admission_rejections_total",
        "Admission rejections by fixed service and reason.",
        "counter",
        (
            ("service", ("qr", "natneg", "peerchat", "server_browser")),
            (
                "reason",
                (
                    "connection_limit",
                    "global_rate_limit",
                    "source_rate_limit",
                    "source_ban_started",
                    "source_banned",
                    "source_table_full",
                ),
            ),
        ),
    ),
    _MetricSpec(
        "fruitspy_protocol_rejections_total",
        "Protocol rejections by fixed service and reason.",
        "counter",
        (
            ("service", ("qr", "natneg", "peerchat", "server_browser")),
            (
                "reason",
                ("malformed", "amplification", "capacity", "budget", "draining"),
            ),
        ),
    ),
    _MetricSpec(
        "fruitspy_natneg_setup_seconds",
        "NatNeg setup latency by established path.",
        "histogram",
        (("path", ("direct", "relay")),),
        (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 30.0),
    ),
)


class MetricsRegistry:
    def __init__(self) -> None:
        self._specs = {spec.name: spec for spec in _METRICS}
        self._values: dict[str, dict[tuple[str, ...], float]] = {}
        self._histograms: dict[str, dict[tuple[str, ...], _HistogramValue]] = {}
        for spec in _METRICS:
            keys = list(product(*(values for _, values in spec.labels))) or [()]
            if spec.kind == "histogram":
                self._histograms[spec.name] = {
                    key: _HistogramValue([0] * len(spec.buckets)) for key in keys
                }
            else:
                self._values[spec.name] = {key: 0.0 for key in keys}

    def increment(self, name: str, amount: int = 1, **labels: str) -> None:
        spec, key = self._metric_key(name, labels, "counter")
        if amount < 0:
            raise ValueError("counter increment must not be negative")
        self._values[spec.name][key] += amount

    def set_gauge(self, name: str, value: int, **labels: str) -> None:
        spec, key = self._metric_key(name, labels, "gauge")
        if value < 0:
            raise ValueError("gauge value must not be negative")
        self._values[spec.name][key] = float(value)

    def observe(self, name: str, value: float, **labels: str) -> None:
        spec, key = self._metric_key(name, labels, "histogram")
        if value < 0:
            raise ValueError("histogram observation must not be negative")
        histogram = self._histograms[spec.name][key]
        histogram.count += 1
        histogram.total += value
        for index, upper_bound in enumerate(spec.buckets):
            if value <= upper_bound:
                histogram.buckets[index] += 1

    def render(self) -> bytes:
        lines: list[str] = []
        for spec in _METRICS:
            lines.append(f"# HELP {spec.name} {spec.help}")
            lines.append(f"# TYPE {spec.name} {spec.kind}")
            if spec.kind == "histogram":
                self._render_histogram(lines, spec)
            else:
                for key, value in self._values[spec.name].items():
                    lines.append(
                        f"{spec.name}{self._render_labels(spec, key)} "
                        f"{self._format_number(value)}"
                    )
        return ("\n".join(lines) + "\n").encode("utf-8")

    def _metric_key(
        self,
        name: str,
        labels: dict[str, str],
        expected_kind: str,
    ) -> tuple[_MetricSpec, tuple[str, ...]]:
        spec = self._specs.get(name)
        if spec is None or spec.kind != expected_kind:
            raise ValueError(f"unknown {expected_kind} metric: {name}")
        expected_labels = tuple(label_name for label_name, _ in spec.labels)
        if set(labels) != set(expected_labels):
            raise ValueError(f"invalid labels for metric: {name}")
        key = tuple(labels[label_name] for label_name in expected_labels)
        for value, (_, allowed_values) in zip(key, spec.labels, strict=True):
            if value not in allowed_values:
                raise ValueError(f"unsupported label value for metric: {name}")
        return spec, key

    def _render_histogram(self, lines: list[str], spec: _MetricSpec) -> None:
        for key, histogram in self._histograms[spec.name].items():
            labels = {
                label_name: value
                for (label_name, _), value in zip(spec.labels, key, strict=True)
            }
            for upper_bound, count in zip(
                spec.buckets,
                histogram.buckets,
                strict=True,
            ):
                bucket_labels = {**labels, "le": self._format_number(upper_bound)}
                lines.append(
                    f"{spec.name}_bucket{self._render_label_mapping(bucket_labels)} "
                    f"{count}"
                )
            infinite_labels = {**labels, "le": "+Inf"}
            lines.append(
                f"{spec.name}_bucket{self._render_label_mapping(infinite_labels)} "
                f"{histogram.count}"
            )
            rendered_labels = self._render_label_mapping(labels)
            lines.append(
                f"{spec.name}_sum{rendered_labels} "
                f"{self._format_number(histogram.total)}"
            )
            lines.append(f"{spec.name}_count{rendered_labels} {histogram.count}")

    @staticmethod
    def _render_labels(spec: _MetricSpec, key: tuple[str, ...]) -> str:
        return MetricsRegistry._render_label_mapping(
            {
                label_name: value
                for (label_name, _), value in zip(spec.labels, key, strict=True)
            }
        )

    @staticmethod
    def _render_label_mapping(labels: dict[str, str]) -> str:
        if not labels:
            return ""
        body = ",".join(
            f'{name}="{value}"'
            for name, value in labels.items()
        )
        return "{" + body + "}"

    @staticmethod
    def _format_number(value: float) -> str:
        return str(int(value)) if value.is_integer() else repr(value)


class MetricsHttpServer:
    REQUEST_TIMEOUT_SECONDS = 2

    def __init__(self, registry: MetricsRegistry) -> None:
        self.registry = registry

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                self.REQUEST_TIMEOUT_SECONDS,
            )
            request_line = request.split(b"\r\n", 1)[0].split()
            if len(request_line) != 3:
                await self._respond(writer, 400, b"Bad Request\n")
            elif request_line[0] != b"GET":
                await self._respond(writer, 405, b"Method Not Allowed\n")
            elif request_line[1] != b"/metrics":
                await self._respond(writer, 404, b"Not Found\n")
            else:
                await self._respond(writer, 200, self.registry.render())
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            await self._respond(writer, 400, b"Bad Request\n")
        except ConnectionError:
            pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                writer.transport.abort()

    @staticmethod
    async def _respond(
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
    ) -> None:
        reason = {
            200: "OK",
            400: "Bad Request",
            404: "Not Found",
            405: "Method Not Allowed",
        }[status]
        content_type = (
            "text/plain; version=0.0.4; charset=utf-8"
            if status == 200
            else "text/plain; charset=utf-8"
        )
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "X-Content-Type-Options: nosniff\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii")
        writer.write(headers + body)
        await writer.drain()
