from __future__ import annotations

import json
import socket
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
class TimeoutConfig:
    reported_server_seconds: int
    nat_session_seconds: int


@dataclass(frozen=True, slots=True)
class ServerConfig:
    bind_host: str
    advertise_host: str
    game: GameConfig
    ports: PortConfig
    timeouts: TimeoutConfig


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
    game = GameConfig(**raw["game"])
    ports = PortConfig(**raw["ports"])
    timeouts = TimeoutConfig(**raw["timeouts"])
    advertise_host = raw["advertise_host"]
    if advertise_host == "auto":
        advertise_host = _local_address()
    if game.name != "FruitNinjaand":
        raise ValueError("Fruit Ninja 1.7.6 requires game name FruitNinjaand")
    if len(game.secret_key) != 6:
        raise ValueError("GameSpy secret keys must be six bytes")
    for value in (ports.availability_qr_udp, ports.peerchat_tcp, ports.server_browser_tcp, ports.natneg_udp):
        if not 1 <= value <= 65535:
            raise ValueError(f"invalid port: {value}")
    return ServerConfig(
        bind_host=raw["bind_host"],
        advertise_host=advertise_host,
        game=game,
        ports=ports,
        timeouts=timeouts,
    )
