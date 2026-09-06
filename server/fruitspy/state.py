from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

LOG = logging.getLogger(__name__)

Address = tuple[str, int]


@dataclass(slots=True)
class ReportedServer:
    source: Address
    instance_key: bytes
    keys: dict[str, str] = field(default_factory=dict)
    challenge: str = ""
    registered: bool = False
    last_seen: float = field(default_factory=time.monotonic)
    registered_at: float | None = None

    def browser_endpoint(self) -> Address:
        return self.source


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
    paired_at: float | None = None
    successful_reports: set[int] = field(default_factory=set)

class NatPeerClaimRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

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
        self._server_change_events: set[asyncio.Event] = set()

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
        changed = server.instance_key != instance_key or server.keys != keys
        server.instance_key = instance_key
        server.keys = keys
        server.last_seen = time.monotonic()
        if server.registered and changed:
            self._notify_server_change()
        if keys.get("statechanged") == "2":
            self.remove_server(source)
        return server

    def register_server(self, source: Address) -> ReportedServer | None:
        server = self.reported_servers.get(source)
        if server is not None:
            first_registration = not server.registered
            server.registered = True
            server.last_seen = time.monotonic()
            if first_registration:
                server.registered_at = server.last_seen
                self._notify_server_change()
        return server

    def remove_server(self, source: Address) -> None:
        server = self.reported_servers.pop(source, None)
        if server is not None and server.registered:
            self._notify_server_change()

    def subscribe_server_changes(self) -> asyncio.Event:
        event = asyncio.Event()
        self._server_change_events.add(event)
        return event

    def unsubscribe_server_changes(self, event: asyncio.Event) -> None:
        self._server_change_events.discard(event)

    def _notify_server_change(self) -> None:
        for event in self._server_change_events:
            event.set()

    def active_servers(self, game_name: str) -> list[ReportedServer]:
        self.expire()
        accepted_games = {game_name, f"{game_name}am"}
        servers = [
            server
            for server in self.reported_servers.values()
            if server.registered and server.keys.get("gamename") in accepted_games
        ]
        return sorted(
            servers,
            key=lambda server: (
                server.registered_at if server.registered_at is not None else float("inf"),
                server.source,
            ),
        )

    def claim_nat_peer(
        self,
        cookie: bytes,
        client_index: int,
        address: Address,
        version: int,
    ) -> tuple[NatSession, str]:
        self.expire()
        session = self.nat_sessions.get(cookie)
        if session is None:
            if len(self.nat_sessions) >= self.max_nat_sessions:
                raise ValueError("NatNeg session capacity reached")
            session = NatSession(cookie=cookie)
            self.nat_sessions[cookie] = session
            event = "session_created"
        else:
            peer = session.peers.get(client_index)
            if peer is not None and peer.address == address:
                peer.version = version
                peer.last_seen = time.monotonic()
                session.last_seen = peer.last_seen
                return session, "peer_refreshed"
            if len(session.peers) == 2:
                raise NatPeerClaimRejected("session_full")
            if peer is not None:
                raise NatPeerClaimRejected("index_claimed")
            if any(peer.address == address for peer in session.peers.values()):
                raise NatPeerClaimRejected("endpoint_claimed")
            event = "peer_added"
        session.peers[client_index] = NatPeer(
            address=address,
            version=version,
            cookie=cookie,
            client_index=client_index,
        )
        session.last_seen = time.monotonic()
        return session, event

    def expire(self) -> tuple[int, int]:
        if not self.reported_servers and not self.nat_sessions:
            return 0, 0
        now = time.monotonic()
        reported_count = len(self.reported_servers)
        nat_count = len(self.nat_sessions)
        retained_servers = {
            address: server
            for address, server in self.reported_servers.items()
            if now - server.last_seen <= self.reported_server_ttl
        }
        server_list_changed = any(
            server.registered and address not in retained_servers
            for address, server in self.reported_servers.items()
        )
        self.reported_servers = retained_servers
        self.nat_sessions = {
            cookie: session
            for cookie, session in self.nat_sessions.items()
            if now - session.last_seen <= self.nat_session_ttl
        }
        if server_list_changed:
            self._notify_server_change()
        return (
            reported_count - len(self.reported_servers),
            nat_count - len(self.nat_sessions),
        )

    async def expire_periodically(self, interval_seconds: int | float) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            reported, nat = self.expire()
            if reported or nat:
                LOG.info(
                    "service=state event=expired reported_servers=%d "
                    "nat_sessions=%d active_reported_servers=%d "
                    "active_nat_sessions=%d",
                    reported,
                    nat,
                    len(self.reported_servers),
                    len(self.nat_sessions),
                )
