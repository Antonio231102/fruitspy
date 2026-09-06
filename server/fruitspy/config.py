from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GameConfig:
    name: str
    secret_key: str
    default_query_port: int
    server_browser_version: int
    max_players: int


@dataclass(frozen=True, slots=True)
class PortConfig:
    availability_qr_udp: int
    peerchat_tcp: int
    server_browser_tcp: int
    natneg_udp: int


@dataclass(frozen=True, slots=True)
class LimitConfig:
    peerchat_line_bytes: int
    server_browser_frame_bytes: int
    qr_packet_bytes: int
    natneg_packet_bytes: int
    peerchat_connections: int
    server_browser_connections: int
    connections_per_source: int
    udp_packets_per_second: int
    udp_burst: int
    udp_tracked_sources: int
    reported_servers: int
    nat_sessions: int


@dataclass(frozen=True, slots=True)
class TimeoutConfig:
    reported_server_seconds: int
    nat_session_seconds: int
    peerchat_handshake_seconds: int
    server_browser_idle_seconds: int
    rate_limit_entry_seconds: int


@dataclass(frozen=True, slots=True)
class RelayConfig:
    policy: str
    fallback_seconds: float
    session_seconds: int
    packet_bytes: int
    bytes_per_second: int
    byte_burst: int
    sessions: int



@dataclass(frozen=True, slots=True)
class ServerConfig:
    bind_host: str
    game: GameConfig
    ports: PortConfig
    timeouts: TimeoutConfig
    limits: LimitConfig
    relay: RelayConfig

def load_config(path: str | Path) -> ServerConfig:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    expected_fields = {"bind_host", "game", "ports", "timeouts", "limits", "relay"}
    unknown_fields = sorted(set(raw) - expected_fields)
    if unknown_fields:
        raise ValueError(f"unknown top-level configuration fields: {', '.join(unknown_fields)}")
    game = GameConfig(**raw["game"])
    ports = PortConfig(**raw["ports"])
    timeouts = TimeoutConfig(**raw["timeouts"])
    limits = LimitConfig(**raw["limits"])
    relay = RelayConfig(**raw["relay"])
    if game.name != "FruitNinjaand":
        raise ValueError("Fruit Ninja 1.7.6 requires game name FruitNinjaand")
    if len(game.secret_key) != 6:
        raise ValueError("GameSpy secret keys must be six bytes")
    for value in (
        ports.availability_qr_udp,
        ports.peerchat_tcp,
        ports.server_browser_tcp,
        ports.natneg_udp,
    ):
        if not 1 <= value <= 65535:
            raise ValueError(f"invalid port: {value}")
    if not 512 <= limits.peerchat_line_bytes <= 65535:
        raise ValueError("peerchat_line_bytes must be between 512 and 65535")
    if not 3 <= limits.server_browser_frame_bytes <= 65535:
        raise ValueError("server_browser_frame_bytes must be between 3 and 65535")
    if not 64 <= limits.qr_packet_bytes <= 65507:
        raise ValueError("qr_packet_bytes must be between 64 and 65507")
    if not 21 <= limits.natneg_packet_bytes <= 65507:
        raise ValueError("natneg_packet_bytes must be between 21 and 65507")
    if not 1 <= limits.peerchat_connections <= 65535:
        raise ValueError("peerchat_connections must be between 1 and 65535")
    if not 1 <= limits.server_browser_connections <= 65535:
        raise ValueError("server_browser_connections must be between 1 and 65535")
    if not 1 <= limits.connections_per_source <= min(
        limits.peerchat_connections,
        limits.server_browser_connections,
    ):
        raise ValueError("connections_per_source must fit the configured connection limits")
    if not 1 <= limits.udp_packets_per_second <= 65535:
        raise ValueError("udp_packets_per_second must be between 1 and 65535")
    if not limits.udp_packets_per_second <= limits.udp_burst <= 65535:
        raise ValueError("udp_burst must be at least udp_packets_per_second")
    if not 1 <= limits.udp_tracked_sources <= 1_000_000:
        raise ValueError("udp_tracked_sources must be between 1 and 1000000")
    if not 1 <= limits.reported_servers <= 1_000_000:
        raise ValueError("reported_servers must be between 1 and 1000000")
    if not 1 <= limits.nat_sessions <= 1_000_000:
        raise ValueError("nat_sessions must be between 1 and 1000000")
    if relay.policy not in {"direct", "auto"}:
        raise ValueError("relay policy must be direct or auto")
    if not 0.1 <= relay.fallback_seconds <= 30:
        raise ValueError("relay fallback_seconds must be between 0.1 and 30")
    if not 10 <= relay.session_seconds <= 86400:
        raise ValueError("relay session_seconds must be between 10 and 86400")
    if not 64 <= relay.packet_bytes <= 65507:
        raise ValueError("relay packet_bytes must be between 64 and 65507")
    if not 1024 <= relay.bytes_per_second <= 100_000_000:
        raise ValueError("relay bytes_per_second must be between 1024 and 100000000")
    if not relay.bytes_per_second <= relay.byte_burst <= 100_000_000:
        raise ValueError("relay byte_burst must be at least bytes_per_second")
    if not 1 <= relay.sessions <= 100_000:
        raise ValueError("relay sessions must be between 1 and 100000")
    for name, value in (
        ("reported_server_seconds", timeouts.reported_server_seconds),
        ("nat_session_seconds", timeouts.nat_session_seconds),
        ("peerchat_handshake_seconds", timeouts.peerchat_handshake_seconds),
        ("server_browser_idle_seconds", timeouts.server_browser_idle_seconds),
        ("rate_limit_entry_seconds", timeouts.rate_limit_entry_seconds),
    ):
        if not 1 <= value <= 86400:
            raise ValueError(f"{name} must be between 1 and 86400")
    return ServerConfig(
        bind_host=raw["bind_host"],
        game=game,
        ports=ports,
        timeouts=timeouts,
        limits=limits,
        relay=relay,
    )
