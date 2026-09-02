from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


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

@dataclass(frozen=True, slots=True)
class TimeoutConfig:
    reported_server_seconds: int
    nat_session_seconds: int


@dataclass(frozen=True, slots=True)
class ServerConfig:
    mode: Literal["lan", "internet"]
    bind_host: str
    advertise_host: str
    game: GameConfig
    ports: PortConfig
    timeouts: TimeoutConfig
    limits: LimitConfig


def _local_address() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        sock.close()


def load_config(path: str | Path) -> ServerConfig:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    mode = raw["mode"]
    game = GameConfig(**raw["game"])
    ports = PortConfig(**raw["ports"])
    timeouts = TimeoutConfig(**raw["timeouts"])
    limits = LimitConfig(**raw["limits"])
    advertise_host = raw["advertise_host"]
    if mode not in {"lan", "internet"}:
        raise ValueError("mode must be 'lan' or 'internet'")
    if mode == "internet" and advertise_host == "auto":
        raise ValueError("internet mode requires an explicit advertise_host")
    if advertise_host == "auto":
        advertise_host = _local_address()
    elif not advertise_host.isascii() or not advertise_host.strip() or "\x00" in advertise_host:
        raise ValueError("advertise_host must be a non-empty ASCII hostname or IPv4 address")
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
    return ServerConfig(
        mode=mode,
        bind_host=raw["bind_host"],
        advertise_host=advertise_host,
        game=game,
        ports=ports,
        timeouts=timeouts,
        limits=limits,
    )
