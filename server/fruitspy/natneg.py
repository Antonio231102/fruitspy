from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from dataclasses import dataclass, field

from .admission import SourceRateLimiter
from .config import ServerConfig
from .state import Address, NatPeerClaimRejected, ServerState

LOG = logging.getLogger(__name__)
MAGIC = b"\xfd\xfc\x1e\x66\x6a\xb2"
NN_INIT = 0
NN_INIT_ACK = 1
NN_ERT_TEST = 2
NN_CONNECT_PING = 7
NN_CONNECT = 5
NN_CONNECT_ACK = 6
NN_ADDRESS_CHECK = 10
NN_ADDRESS_REPLY = 11
NN_NATIFY_REQUEST = 12
NN_REPORT = 13
NN_REPORT_ACK = 14
NN_PREINIT = 15
NN_PREINIT_ACK = 16

REPORT_RESULTS = ("failure", "success")
NAT_TYPES = (
    "none",
    "firewall_only",
    "full_cone",
    "restricted_cone",
    "port_restricted_cone",
    "symmetric",
    "unknown",
)
MAPPING_SCHEMES = (
    "unrecognized",
    "private_as_public",
    "consistent_port",
    "incremental",
    "mixed",
)


@dataclass(slots=True)
class _RelayEndpoint:
    index: int
    address: Address
    tokens: float
    last_refill: float

    def allow(self, size: int, rate: int, burst: int, now: float) -> bool:
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(float(burst), self.tokens + elapsed * rate)
        self.last_refill = now
        if size > self.tokens:
            return False
        self.tokens -= size
        return True


@dataclass(slots=True)
class _RelaySession:
    cookie: bytes
    endpoints: dict[int, _RelayEndpoint]
    started_at: float
    last_seen: float
    reports: set[int] = field(default_factory=set)
    last_metrics_at: float = 0.0
    ready: set[int] = field(default_factory=set)
    packets: int = 0
    bytes: int = 0
    drops: int = 0
    established: bool = False


def _enum_name(names: tuple[str, ...], value: int) -> str:
    return names[value] if value < len(names) else "unknown"


class NatNegProtocol(asyncio.DatagramProtocol):
    def __init__(self, config: ServerConfig, state: ServerState) -> None:
        self.config = config
        self.state = state
        self.transport: asyncio.DatagramTransport | None = None
        self.rate_limiter = SourceRateLimiter(
            config.limits.udp_packets_per_second,
            config.limits.udp_burst,
            config.limits.udp_tracked_sources,
            config.timeouts.rate_limit_entry_seconds,
        )
        self._relay_expirations: dict[bytes, asyncio.TimerHandle] = {}
        self._fallbacks: dict[bytes, asyncio.TimerHandle] = {}
        self._relays: dict[bytes, _RelaySession] = {}
        self._relay_by_address: dict[Address, tuple[bytes, int]] = {}
        self._next_relay_expiry = 0.0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def connection_lost(self, exc: Exception | None) -> None:
        for handle in self._fallbacks.values():
            handle.cancel()
        self._fallbacks.clear()
        for handle in self._relay_expirations.values():
            handle.cancel()
        self._relay_expirations.clear()
        for cookie in list(self._relays):
            self._finish_relay(cookie, "server_stopped")
        self.transport = None

    def datagram_received(self, data: bytes, addr: Address) -> None:
        if not self.rate_limiter.allow(addr[0]):
            LOG.debug("NatNeg rate limit source=%s", addr[0])
            return
        now = time.monotonic()
        self._expire_relays(now)
        if self._handle_relay_ping(data, addr, now):
            return
        if not data.startswith(MAGIC):
            self._forward_relay(data, addr, now)
            return
        try:
            responses = self.handle_datagram(data, addr)
        except (ValueError, OSError) as error:
            LOG.warning("discarding malformed NatNeg packet from %s: %s", addr, error)
            return
        if self.transport is not None:
            for response, destination in responses:
                self.transport.sendto(response, destination)

    def handle_datagram(self, data: bytes, addr: Address) -> list[tuple[bytes, Address]]:
        if len(data) > self.config.limits.natneg_packet_bytes:
            raise ValueError("NatNeg packet exceeds configured limit")
        if len(data) < 12 or data[:6] != MAGIC:
            raise ValueError("invalid NatNeg header")
        version = data[6]
        packet_type = data[7]
        cookie = data[8:12]
        if packet_type == NN_INIT:
            if len(data) < 21:
                raise ValueError("short NatNeg init")
            port_type = data[12]
            client_index = data[13]
            use_game_port = data[14]
            if client_index not in (0, 1):
                raise ValueError(f"invalid NatNeg client index: {client_index}")
            local_endpoint = (
                socket.inet_ntoa(data[15:19]),
                struct.unpack_from(">H", data, 19)[0],
            )
            relay = self._relays.get(cookie)
            if relay is not None:
                endpoint = relay.endpoints.get(client_index)
                if endpoint is None or endpoint.address != addr:
                    LOG.warning(
                        "service=natneg event=peer_rejected session=%s peer=%d "
                        "source=%s reason=relay_active",
                        cookie.hex(),
                        client_index,
                        addr,
                    )
                    return []
                relay.last_seen = time.monotonic()
                LOG.info(
                    "service=natneg event=peer_refreshed session=%s peer=%d source=%s "
                    "port_type=%d use_game_port=%s local_endpoint=%s",
                    cookie.hex(),
                    client_index,
                    addr,
                    port_type,
                    bool(use_game_port),
                    local_endpoint,
                )
                return [(self._with_type(data, NN_INIT_ACK), addr)]
            # A stock INIT proves only cookie and index knowledge. Preserve the first
            # accepted endpoint for each index; conflicting claims must not mutate it.
            try:
                session, event = self.state.claim_nat_peer(
                    cookie,
                    client_index,
                    addr,
                    version,
                )
            except NatPeerClaimRejected as error:
                LOG.warning(
                    "service=natneg event=peer_rejected session=%s peer=%d "
                    "source=%s reason=%s",
                    cookie.hex(),
                    client_index,
                    addr,
                    error.reason,
                )
                return []
            LOG.info(
                "service=natneg event=%s session=%s peer=%d source=%s "
                "port_type=%d use_game_port=%s local_endpoint=%s",
                event,
                cookie.hex(),
                client_index,
                addr,
                port_type,
                bool(use_game_port),
                local_endpoint,
            )
            responses = [(self._with_type(data, NN_INIT_ACK), addr)]
            if 0 in session.peers and 1 in session.peers:
                for index, peer in session.peers.items():
                    other = session.peers[1 - index]
                    responses.append(
                        (self._connect_packet(peer.version, cookie, other.address), peer.address)
                    )
                if session.paired_at is None:
                    session.paired_at = time.monotonic()
                    LOG.info(
                        "service=natneg event=peers_paired session=%s peers=%s",
                        cookie.hex(),
                        [peer.address for peer in session.peers.values()],
                    )
                    self._schedule_fallback(cookie)
            return responses
        if packet_type == NN_ADDRESS_CHECK:
            LOG.debug(
                "service=natneg event=address_check session=%s source=%s",
                cookie.hex(),
                addr,
            )
            reply = bytearray(self._with_type(data, NN_ADDRESS_REPLY))
            if len(reply) < 21:
                reply.extend(b"\x00" * (21 - len(reply)))
            reply[15:19] = socket.inet_aton(addr[0])
            reply[19:21] = struct.pack(">H", addr[1])
            return [(bytes(reply), addr)]
        if packet_type == NN_NATIFY_REQUEST:
            LOG.debug(
                "service=natneg event=natify_request session=%s source=%s",
                cookie.hex(),
                addr,
            )
            return [(self._with_type(data, NN_ERT_TEST), addr)]
        if packet_type == NN_REPORT:
            if len(data) < 23:
                raise ValueError("short NatNeg report")
            client_index = data[13]
            result = data[14]
            if client_index not in (0, 1):
                raise ValueError(f"invalid NatNeg report client index: {client_index}")
            if result not in (0, 1):
                raise ValueError(f"invalid NatNeg report result: {result}")
            nat_type, mapping_scheme = struct.unpack_from("<II", data, 15)
            LOG.info(
                "service=natneg event=client_report session=%s source=%s "
                "peer=%d result=%s result_code=%d nat_type=%s nat_type_code=%d "
                "mapping=%s mapping_code=%d",
                cookie.hex(),
                addr,
                client_index,
                REPORT_RESULTS[result],
                result,
                _enum_name(NAT_TYPES, nat_type),
                nat_type,
                _enum_name(MAPPING_SCHEMES, mapping_scheme),
                mapping_scheme,
            )
            self._record_report(cookie, client_index, bool(result), addr)
            return [(self._with_type(data, NN_REPORT_ACK), addr)]
        if packet_type == NN_PREINIT:
            LOG.debug(
                "service=natneg event=preinit session=%s source=%s",
                cookie.hex(),
                addr,
            )
            reply = bytearray(self._with_type(data, NN_PREINIT_ACK))
            if len(reply) > 13:
                reply[13] = 2
            return [(bytes(reply), addr)]
        if packet_type == NN_CONNECT_ACK:
            LOG.debug(
                "service=natneg event=connect_ack session=%s source=%s",
                cookie.hex(),
                addr,
            )
            return []
        return []

    def _schedule_fallback(self, cookie: bytes) -> None:
        if (
            self.config.relay.policy != "auto"
            or self.transport is None
            or cookie in self._fallbacks
        ):
            return
        delay = self.config.relay.fallback_seconds
        self._fallbacks[cookie] = asyncio.get_running_loop().call_later(
            delay,
            self._activate_relay,
            cookie,
        )
        LOG.info(
            "service=natneg event=relay_fallback_scheduled session=%s delay_ms=%d",
            cookie.hex(),
            round(delay * 1000),
        )

    def _record_report(
        self,
        cookie: bytes,
        client_index: int,
        success: bool,
        addr: Address,
    ) -> None:
        session = self.state.nat_sessions.get(cookie)
        if session is None:
            return
        peer = session.peers.get(client_index)
        if peer is None or peer.address != addr:
            LOG.warning(
                "service=natneg event=report_rejected session=%s peer=%d source=%s",
                cookie.hex(),
                client_index,
                addr,
            )
            return
        now = time.monotonic()
        session.last_seen = now
        relay = self._relays.get(cookie)
        if relay is not None and success and not relay.ready:
            LOG.info(
                "service=natneg event=relay_race_resolved session=%s "
                "outcome=direct peer=%d relay_age_ms=%d",
                cookie.hex(),
                client_index,
                round((now - relay.started_at) * 1000),
            )
            self._finish_relay(cookie, "direct_success_race")
            relay = None
        if relay is not None:
            if success:
                relay.reports.add(client_index)
                if relay.reports == {0, 1} and not relay.established:
                    relay.established = True
                    LOG.info(
                        "service=natneg event=relay_established session=%s "
                        "setup_ms=%d ready_peers=%d",
                        cookie.hex(),
                        round((time.monotonic() - relay.started_at) * 1000),
                        len(relay.ready),
                    )
            return
        if not success or client_index in session.successful_reports:
            return
        session.successful_reports.add(client_index)
        handle = self._fallbacks.pop(cookie, None)
        if handle is not None:
            handle.cancel()
        elapsed = (
            round((time.monotonic() - session.paired_at) * 1000)
            if session.paired_at is not None
            else 0
        )
        LOG.info(
            "service=natneg event=direct_established session=%s peer=%d setup_ms=%d",
            cookie.hex(),
            client_index,
            elapsed,
        )

    def _activate_relay(self, cookie: bytes) -> None:
        self._fallbacks.pop(cookie, None)
        if self.transport is None or self.config.relay.policy != "auto":
            return
        session = self.state.nat_sessions.get(cookie)
        if (
            session is None
            or set(session.peers) != {0, 1}
            or session.successful_reports
            or cookie in self._relays
        ):
            return
        now = time.monotonic()
        self._expire_relays(now, force=True)
        if len(self._relays) >= self.config.relay.sessions:
            LOG.warning(
                "service=natneg event=relay_unavailable session=%s reason=capacity",
                cookie.hex(),
            )
            return
        addresses = {peer.address for peer in session.peers.values()}
        if len(addresses) != 2:
            LOG.warning(
                "service=natneg event=relay_unavailable session=%s "
                "reason=endpoint_collision",
                cookie.hex(),
            )
            return
        replaced = {
            binding[0]
            for address in addresses
            if (binding := self._relay_by_address.get(address)) is not None
        }
        for replaced_cookie in replaced:
            self._finish_relay(replaced_cookie, "endpoint_reused")
        relay = _RelaySession(
            cookie=cookie,
            endpoints={
                index: _RelayEndpoint(
                    index=index,
                    address=peer.address,
                    tokens=float(self.config.relay.byte_burst),
                    last_refill=now,
                )
                for index, peer in session.peers.items()
            },
            started_at=now,
            last_seen=now,
        )
        self._relays[cookie] = relay
        self._relay_expirations[cookie] = asyncio.get_running_loop().call_later(
            self.config.relay.session_seconds,
            self._finish_relay,
            cookie,
            "ttl",
        )
        for index, endpoint in relay.endpoints.items():
            self._relay_by_address[endpoint.address] = (cookie, index)
            version = session.peers[index].version
            self.transport.sendto(
                self._relay_ping_packet(version, cookie),
                endpoint.address,
            )
        paired_at = session.paired_at if session.paired_at is not None else now
        LOG.info(
            "service=natneg event=relay_activated session=%s fallback_ms=%d "
            "active_relays=%d",
            cookie.hex(),
            round((now - paired_at) * 1000),
            len(self._relays),
        )

    def _handle_relay_ping(self, data: bytes, addr: Address, now: float) -> bool:
        if len(data) < 20 or data[:6] != MAGIC or data[7] != NN_CONNECT_PING:
            return False
        cookie = data[8:12]
        relay = self._relays.get(cookie)
        binding = self._relay_by_address.get(addr)
        if relay is None or binding is None or binding[0] != cookie:
            return False
        index = binding[1]
        if relay.endpoints[index].address != addr:
            return False
        is_new_peer = index not in relay.ready
        was_ready = relay.ready == {0, 1}
        relay.ready.add(index)
        relay.last_seen = now
        session = self.state.nat_sessions.get(cookie)
        if session is not None:
            session.last_seen = now
        if is_new_peer:
            LOG.info(
                "service=natneg event=relay_peer_ready session=%s peer=%d",
                cookie.hex(),
                index,
            )
        if not was_ready and relay.ready == {0, 1}:
            LOG.info(
                "service=natneg event=relay_ready session=%s setup_ms=%d",
                cookie.hex(),
                round((now - relay.started_at) * 1000),
            )
        return True

    def _forward_relay(self, data: bytes, addr: Address, now: float) -> bool:
        binding = self._relay_by_address.get(addr)
        if binding is None:
            LOG.debug("service=natneg event=relay_packet_rejected source=%s", addr)
            return False
        cookie, index = binding
        relay = self._relays.get(cookie)
        if relay is None:
            self._relay_by_address.pop(addr, None)
            return False
        relay.last_seen = now
        session = self.state.nat_sessions.get(cookie)
        if session is not None:
            session.last_seen = now
        endpoint = relay.endpoints[index]
        if len(data) > self.config.relay.packet_bytes:
            relay.drops += 1
            LOG.debug(
                "service=natneg event=relay_packet_dropped session=%s "
                "peer=%d reason=packet_size bytes=%d",
                cookie.hex(),
                index,
                len(data),
            )
            return True
        if relay.ready != {0, 1}:
            relay.drops += 1
            return True
        if not endpoint.allow(
            len(data),
            self.config.relay.bytes_per_second,
            self.config.relay.byte_burst,
            now,
        ):
            relay.drops += 1
            LOG.debug(
                "service=natneg event=relay_packet_dropped session=%s "
                "peer=%d reason=byte_rate",
                cookie.hex(),
                index,
            )
            return True
        destination = relay.endpoints[1 - index].address
        if self.transport is None:
            relay.drops += 1
            return True
        self.transport.sendto(data, destination)
        relay.packets += 1
        relay.bytes += len(data)
        if relay.packets == 1:
            relay.last_metrics_at = now
            LOG.info(
                "service=natneg event=relay_forwarding session=%s",
                cookie.hex(),
            )
        elif now - relay.last_metrics_at >= 30:
            relay.last_metrics_at = now
            LOG.info(
                "service=natneg event=relay_metrics session=%s "
                "duration_ms=%d packets=%d bytes=%d drops=%d",
                cookie.hex(),
                round((now - relay.started_at) * 1000),
                relay.packets,
                relay.bytes,
                relay.drops,
            )
        return True

    def _expire_relays(self, now: float, *, force: bool = False) -> None:
        if not force and now < self._next_relay_expiry:
            return
        self._next_relay_expiry = now + 1
        lifetime = self.config.relay.session_seconds
        for cookie, relay in list(self._relays.items()):
            if now - relay.last_seen > lifetime:
                self._finish_relay(cookie, "idle_timeout")

    def _finish_relay(self, cookie: bytes, reason: str) -> None:
        relay = self._relays.pop(cookie, None)
        if relay is None:
            return
        expiration = self._relay_expirations.pop(cookie, None)
        if expiration is not None:
            expiration.cancel()
        for endpoint in relay.endpoints.values():
            if self._relay_by_address.get(endpoint.address) == (cookie, endpoint.index):
                self._relay_by_address.pop(endpoint.address, None)
        handle = self._fallbacks.pop(cookie, None)
        if handle is not None:
            handle.cancel()
        LOG.info(
            "service=natneg event=relay_closed session=%s reason=%s "
            "duration_ms=%d packets=%d bytes=%d drops=%d active_relays=%d",
            cookie.hex(),
            reason,
            round((time.monotonic() - relay.started_at) * 1000),
            relay.packets,
            relay.bytes,
            relay.drops,
            len(self._relays),
        )

    @staticmethod
    def _relay_ping_packet(version: int, cookie: bytes) -> bytes:
        return (
            MAGIC
            + bytes((version, NN_CONNECT_PING))
            + cookie
            + b"\x00" * 6
            + b"\x01\x00"
        )

    @staticmethod
    def _with_type(data: bytes, packet_type: int) -> bytes:
        reply = bytearray(data)
        reply[7] = packet_type
        return bytes(reply)

    @staticmethod
    def _connect_packet(version: int, cookie: bytes, remote: Address) -> bytes:
        return (
            MAGIC
            + bytes((version, NN_CONNECT))
            + cookie
            + socket.inet_aton(remote[0])
            + struct.pack(">H", remote[1])
            + b"\x00\x00"
        )
