from __future__ import annotations

import asyncio
import logging
import secrets
import string
import struct

from .admission import (
    UdpAdmission,
    UdpAdmissionDecision,
    create_udp_admission,
    udp_response_within_amplification_limit,
)
from .config import ServerConfig
from .crypto import gsseckey
from .metrics import MetricsRegistry
from .log_fields import sanitize_log_field
from .state import Address, ServerState

LOG = logging.getLogger(__name__)
QR_MAGIC = b"\xfe\xfd"
PACKET_CHALLENGE = 0x01
PACKET_HEARTBEAT = 0x03
PACKET_KEEPALIVE = 0x08
PACKET_AVAILABLE = 0x09
PACKET_CLIENT_REGISTERED = 0x0A
QR_AMPLIFICATION_NUMERATOR = 2
QR_AMPLIFICATION_DENOMINATOR = 1


def availability_response(status: int = 0) -> bytes:
    return QR_MAGIC + bytes((PACKET_AVAILABLE,)) + struct.pack(">II", 0, status)


def _read_cstring(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise ValueError("unterminated string")
    return data[offset:end].decode("utf-8", "replace"), end + 1


def parse_qr_server_keys(data: bytes, offset: int = 5) -> dict[str, str]:
    keys: dict[str, str] = {}
    while offset < len(data):
        key, offset = _read_cstring(data, offset)
        if not key:
            break
        value, offset = _read_cstring(data, offset)
        keys[key] = value
    return keys


class AvailabilityQRProtocol(asyncio.DatagramProtocol):
    def __init__(
        self,
        config: ServerConfig,
        state: ServerState,
        udp_admission: UdpAdmission | None = None,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.metrics = metrics or state.metrics
        self.transport: asyncio.DatagramTransport | None = None
        self.udp_admission = udp_admission or create_udp_admission(config)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Address) -> None:
        decision = self.udp_admission.allow(addr[0])
        if decision is not UdpAdmissionDecision.ALLOWED:
            log_level = (
                logging.WARNING
                if decision is UdpAdmissionDecision.SOURCE_BAN_STARTED
                else logging.DEBUG
            )
            LOG.log(
                log_level,
                "service=qr event=udp_admission_rejected source=%s reason=%s",
                addr[0],
                decision.value,
            )
            self.metrics.increment(
                "fruitspy_admission_rejections_total",
                service="qr",
                reason=decision.value,
            )
            return
        try:
            response = self.handle_datagram(data, addr)
        except (ValueError, UnicodeError) as error:
            LOG.warning(
                "discarding malformed QR packet from %s: %s",
                addr,
                sanitize_log_field(error),
            )
            self.metrics.increment(
                "fruitspy_protocol_rejections_total",
                service="qr",
                reason="malformed",
            )
            return
        if response is not None and self.transport is not None:
            self.transport.sendto(response, addr)

    def handle_datagram(self, data: bytes, addr: Address) -> bytes | None:
        if len(data) > self.config.limits.qr_packet_bytes:
            raise ValueError("QR packet exceeds configured limit")
        if len(data) < 1:
            raise ValueError("empty packet")
        packet_type = data[0]
        if packet_type == PACKET_AVAILABLE:
            if len(data) < 6:
                raise ValueError("short availability packet")
            game_name, _ = _read_cstring(data, 5)
            status = 0 if game_name == self.config.game.name else 1
            LOG.info(
                "service=qr event=availability source=%s accepted=%s",
                addr,
                status == 0,
            )
            return self._bounded_response(data, availability_response(status), addr)
        if packet_type == PACKET_HEARTBEAT:
            return self._bounded_response(data, self._heartbeat(data, addr), addr)
        if packet_type == PACKET_CHALLENGE:
            return self._bounded_response(
                data,
                self._challenge_response(data, addr),
                addr,
            )
        if packet_type == PACKET_KEEPALIVE:
            if len(data) < 5:
                raise ValueError("short keepalive packet")
            return self._bounded_response(
                data,
                QR_MAGIC + bytes((PACKET_KEEPALIVE,)) + data[1:5],
                addr,
            )
        return None

    def _bounded_response(
        self,
        request: bytes,
        response: bytes | None,
        addr: Address,
    ) -> bytes | None:
        if response is None or udp_response_within_amplification_limit(
            len(request),
            len(response),
            numerator=QR_AMPLIFICATION_NUMERATOR,
            denominator=QR_AMPLIFICATION_DENOMINATOR,
        ):
            return response
        LOG.warning(
            "service=qr event=response_suppressed source=%s "
            "request_bytes=%d response_bytes=%d limit=2",
            addr,
            len(request),
            len(response),
        )
        self.metrics.increment(
            "fruitspy_protocol_rejections_total",
            service="qr",
            reason="amplification",
        )
        return None

    def _heartbeat(self, data: bytes, addr: Address) -> bytes | None:
        if len(data) < 7:
            raise ValueError("short heartbeat")
        instance_key = data[1:5]
        keys = parse_qr_server_keys(data)
        if keys.get("statechanged") == "2":
            self.state.remove_server(addr)
            LOG.info("QR server removed source=%s", addr)
            return None
        game_name = keys.get("gamename")
        accepted_games = {self.config.game.name, f"{self.config.game.name}am"}
        if game_name not in accepted_games:
            LOG.warning("service=qr event=game_rejected source=%s", addr)
            return None
        server = self.state.report_server(addr, instance_key, keys)
        if server.registered:
            return None
        alphabet = string.ascii_letters + string.digits
        server.challenge = "".join(secrets.choice(alphabet) for _ in range(20))
        LOG.info("QR challenge source=%s keys=%d", addr, len(keys))
        return (
            QR_MAGIC
            + bytes((PACKET_CHALLENGE,))
            + instance_key
            + server.challenge.encode("ascii")
            + b"\x00"
        )

    def _challenge_response(self, data: bytes, addr: Address) -> bytes | None:
        if len(data) < 6:
            raise ValueError("short challenge response")
        server = self.state.reported_servers.get(addr)
        if server is None or not server.challenge:
            return None
        response, _ = _read_cstring(data, 5)
        expected = gsseckey(server.challenge, self.config.game.secret_key)
        if not secrets.compare_digest(response, expected):
            LOG.warning("invalid QR challenge response from %s", addr)
            return None
        self.state.register_server(addr)
        LOG.info(
            "QR server registered source=%s browser_endpoint=%s",
            addr,
            server.browser_endpoint(),
        )
        return QR_MAGIC + bytes((PACKET_CLIENT_REGISTERED,)) + server.instance_key
