from __future__ import annotations

import asyncio
import logging
import socket
import struct

from .admission import SourceRateLimiter
from .config import ServerConfig
from .state import Address, ServerState

LOG = logging.getLogger(__name__)
MAGIC = b"\xfd\xfc\x1e\x66\x6a\xb2"
NN_INIT = 0
NN_INIT_ACK = 1
NN_ERT_TEST = 2
NN_CONNECT = 5
NN_CONNECT_ACK = 6
NN_ADDRESS_CHECK = 10
NN_ADDRESS_REPLY = 11
NN_NATIFY_REQUEST = 12
NN_REPORT = 13
NN_REPORT_ACK = 14
NN_PREINIT = 15
NN_PREINIT_ACK = 16

NEGOTIATION_RESULTS = (
    "success",
    "deadbeat_partner",
    "init_timeout",
    "ping_timeout",
    "unknown_error",
    "no_result",
)
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

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Address) -> None:
        if not self.rate_limiter.allow(addr[0]):
            LOG.debug("NatNeg rate limit source=%s", addr[0])
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
            is_new_session = cookie not in self.state.nat_sessions
            session = self.state.touch_nat_peer(cookie, client_index, addr, version)
            LOG.info(
                "service=natneg event=%s session=%s peer=%d source=%s "
                "port_type=%d use_game_port=%s local_endpoint=%s",
                "session_created" if is_new_session else "peer_updated",
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
                    responses.append((self._connect_packet(peer.version, cookie, other.address), peer.address))
                LOG.info(
                    "service=natneg event=peers_paired session=%s peers=%s",
                    cookie.hex(),
                    [peer.address for peer in session.peers.values()],
                )
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
            nat_type, mapping_scheme = struct.unpack_from("<II", data, 15)
            LOG.info(
                "service=natneg event=client_report session=%s source=%s "
                "peer=%d result=%s result_code=%d nat_type=%s nat_type_code=%d "
                "mapping=%s mapping_code=%d",
                cookie.hex(),
                addr,
                client_index,
                _enum_name(NEGOTIATION_RESULTS, result),
                result,
                _enum_name(NAT_TYPES, nat_type),
                nat_type,
                _enum_name(MAPPING_SCHEMES, mapping_scheme),
                mapping_scheme,
            )
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
