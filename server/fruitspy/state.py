from __future__ import annotations

import time
from dataclasses import dataclass, field

Address = tuple[str, int]


@dataclass(slots=True)
class ReportedServer:
    source: Address
    instance_key: bytes
    keys: dict[str, str] = field(default_factory=dict)
    challenge: str = ""
    registered: bool = False
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def reported_game_port(self) -> int:
        value = self.keys.get("hostport") or self.keys.get("localport")
        if value and value.isdecimal():
            port = int(value)
            if 1 <= port <= 65535:
                return port
        return self.source[1]

    def browser_endpoint(self, mode: str) -> Address:
        if mode == "internet":
            return self.source
        return self.source[0], self.reported_game_port


@dataclass(slots=True)
class NatPeer:
    address: Address
    version: int
    cookie: bytes
    client_index: int
    last_seen: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class NatSession:
    cookie: bytes
    peers: dict[int, NatPeer] = field(default_factory=dict)
    last_seen: float = field(default_factory=time.monotonic)


class ServerState:
    def __init__(
        self,
        reported_server_ttl: int,
        nat_session_ttl: int,
        max_reported_servers: int = 2048,
        max_nat_sessions: int = 4096,
    ) -> None:
        self.reported_server_ttl = reported_server_ttl
        self.nat_session_ttl = nat_session_ttl
        self.max_reported_servers = max_reported_servers
        self.max_nat_sessions = max_nat_sessions
        self.reported_servers: dict[Address, ReportedServer] = {}
        self.nat_sessions: dict[bytes, NatSession] = {}

    def report_server(
        self,
        source: Address,
        instance_key: bytes,
        keys: dict[str, str],
    ) -> ReportedServer:
        server = self.reported_servers.get(source)
        if server is None:
            self.expire()
            if len(self.reported_servers) >= self.max_reported_servers:
                raise ValueError("reported server capacity reached")
            server = ReportedServer(source=source, instance_key=instance_key)
            self.reported_servers[source] = server
        server.instance_key = instance_key
        server.keys = keys
        server.last_seen = time.monotonic()
        if keys.get("statechanged") == "2":
            self.reported_servers.pop(source, None)
        return server

    def register_server(self, source: Address) -> ReportedServer | None:
        server = self.reported_servers.get(source)
        if server is not None:
            server.registered = True
            server.last_seen = time.monotonic()
        return server

    def active_servers(self, game_name: str) -> list[ReportedServer]:
        self.expire()
        accepted_games = {game_name, f"{game_name}am"}
        return [
            server
            for server in self.reported_servers.values()
            if server.registered and server.keys.get("gamename") in accepted_games
        ]

    def touch_nat_peer(
        self,
        cookie: bytes,
        client_index: int,
        address: Address,
        version: int,
    ) -> NatSession:
        session = self.nat_sessions.get(cookie)
        if session is None:
            self.expire()
            if len(self.nat_sessions) >= self.max_nat_sessions:
                raise ValueError("NatNeg session capacity reached")
            session = NatSession(cookie=cookie)
            self.nat_sessions[cookie] = session
        session.peers[client_index] = NatPeer(
            address=address,
            version=version,
            cookie=cookie,
            client_index=client_index,
        )
        session.last_seen = time.monotonic()
        return session

    def expire(self) -> None:
        now = time.monotonic()
        self.reported_servers = {
            address: server
            for address, server in self.reported_servers.items()
            if now - server.last_seen <= self.reported_server_ttl
        }
        self.nat_sessions = {
            cookie: session
            for cookie, session in self.nat_sessions.items()
            if now - session.last_seen <= self.nat_session_ttl
        }
