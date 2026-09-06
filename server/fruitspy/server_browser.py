from __future__ import annotations

import asyncio
import logging
import re
import secrets
import socket
import struct
from dataclasses import dataclass, field

from .admission import ConnectionAdmission
from .config import ServerConfig
from .crypto import EnctypeX
from .state import Address, ReportedServer, ServerState

LOG = logging.getLogger(__name__)
SERVER_LIST_REQUEST = 0
SERVER_INFO_REQUEST = 1
PUSH_SERVER_MESSAGE = 2
SEND_MESSAGE_REQUEST = 2
SEND_GROUPS = 32
PUSH_UPDATES = 4
DELETE_SERVER_MESSAGE = 4
CONNECT_NEGOTIATE_FLAG = 4
PRIVATE_IP_FLAG = 2
NONSTANDARD_PORT_FLAG = 16
NONSTANDARD_PRIVATE_PORT_FLAG = 32
HAS_KEYS_FLAG = 64
HAS_FULL_RULES_FLAG = 128


def _read_cstring(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise ValueError("unterminated Server Browser string")
    return data[offset:end].decode("utf-8", "replace"), end + 1


def _field_names(field_list: str) -> list[str]:
    return [field for field in field_list.split("\\") if field]


def _matches_filter(server: ReportedServer, expression: str) -> bool:
    if not expression.strip():
        return True
    clauses = re.split(r"\s+(?:and|AND)\s+", expression.strip())
    for clause in clauses:
        match = re.fullmatch(
            r"\(?\s*([A-Za-z0-9_]+)\s*=\s*'?([^')]+)'?\s*\)?",
            clause,
        )
        if match is None:
            LOG.warning(
                "unsupported Server Browser filter clause bytes=%d",
                len(clause.encode("utf-8")),
            )
            continue
        key, expected = match.groups()
        if server.keys.get(key, "") != expected.strip():
            return False
    return True

@dataclass(slots=True)
class ServerListSubscription:
    query_game: str
    filter_text: str
    known_servers: dict[Address, bytes] = field(default_factory=dict)


class ServerBrowserServer:
    def __init__(self, config: ServerConfig, state: ServerState) -> None:
        self.config = config
        self.state = state
        self.admission = ConnectionAdmission(
            config.limits.server_browser_connections,
            config.limits.connections_per_source,
        )

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        source = str(peer[0]) if peer else "0.0.0.0"
        connection_id = secrets.token_hex(8)
        if not self.admission.acquire(source):
            LOG.warning(
                "service=server_browser event=admission_rejected source=%s",
                source,
            )
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                writer.transport.abort()
            return
        cipher: EnctypeX | None = None
        subscription: ServerListSubscription | None = None
        change_event = self.state.subscribe_server_changes()

        async def push_registered_servers() -> None:
            while True:
                await change_event.wait()
                change_event.clear()
                if cipher is None or subscription is None:
                    continue
                frames = self._subscription_frames(subscription)
                if not frames:
                    continue
                for frame in frames:
                    writer.write(cipher.encrypt(frame))
                await writer.drain()

        push_task = asyncio.create_task(push_registered_servers())
        LOG.info(
            "service=server_browser event=connected connection=%s source=%s",
            connection_id,
            source,
        )
        try:
            while True:
                header = await asyncio.wait_for(
                    reader.readexactly(2),
                    self.config.timeouts.server_browser_idle_seconds,
                )
                size = struct.unpack(">H", header)[0]
                LOG.debug(
                    "service=server_browser event=frame connection=%s bytes=%d",
                    connection_id,
                    size,
                )
                if not 3 <= size <= self.config.limits.server_browser_frame_bytes:
                    raise ValueError(f"invalid Server Browser frame length: {size}")
                payload = bytearray()
                frame_deadline = (
                    asyncio.get_running_loop().time()
                    + self.config.timeouts.server_browser_idle_seconds
                )
                while len(payload) < size - 2:
                    remaining = frame_deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise ValueError("Server Browser frame deadline exceeded")
                    try:
                        chunk = await asyncio.wait_for(
                            reader.read(size - 2 - len(payload)),
                            min(0.1, remaining),
                        )
                    except TimeoutError:
                        break
                    if not chunk:
                        break
                    payload.extend(chunk)
                packet = header + payload
                if len(packet) != size:
                    LOG.info(
                        "service=server_browser event=length_compatibility "
                        "connection=%s declared=%d received=%d",
                        connection_id,
                        size,
                        len(packet),
                    )
                if len(packet) < 3:
                    raise ValueError("truncated Server Browser frame")
                request_type = packet[2]
                if request_type == SERVER_LIST_REQUEST:
                    response, cipher, subscription = self._handle_list_request(
                        packet,
                        source,
                    )
                elif request_type == SERVER_INFO_REQUEST and cipher is not None:
                    response = self._handle_info_request(packet, cipher)
                elif request_type == SEND_MESSAGE_REQUEST:
                    await self._handle_send_message_request(packet)
                    continue
                else:
                    response = None
                if response is None:
                    LOG.info(
                        "service=server_browser event=frame_ignored "
                        "connection=%s type=%d bytes=%d",
                        connection_id,
                        request_type,
                        len(packet),
                    )
                else:
                    writer.write(response)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except TimeoutError:
            LOG.info(
                "service=server_browser event=idle_timeout connection=%s source=%s",
                connection_id,
                source,
            )
        except ValueError as error:
            LOG.warning(
                "service=server_browser event=request_rejected "
                "connection=%s source=%s error=%s",
                connection_id,
                source,
                error,
            )
        finally:
            self.state.unsubscribe_server_changes(change_event)
            push_task.cancel()
            await asyncio.gather(push_task, return_exceptions=True)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                writer.transport.abort()
            self.admission.release(source)
            LOG.info(
                "service=server_browser event=disconnected connection=%s source=%s",
                connection_id,
                source,
            )

    def handle_request(self, packet: bytes, source_ip: str) -> bytes | None:
        response, _, _ = self._handle_list_request(packet, source_ip)
        return response

    def _handle_list_request(
        self,
        packet: bytes,
        source_ip: str,
    ) -> tuple[bytes | None, EnctypeX | None, ServerListSubscription | None]:
        if len(packet) < 5 or packet[2] != SERVER_LIST_REQUEST:
            return None, None, None
        offset = 3
        list_version = packet[offset]
        encoding_version = packet[offset + 1]
        offset += 2
        if list_version != 1 or encoding_version != 3:
            raise ValueError("unsupported Server Browser protocol version")
        if offset + 4 > len(packet):
            raise ValueError("truncated Server Browser request")
        offset += 4  # game version
        query_game, offset = _read_cstring(packet, offset)
        client_game, offset = _read_cstring(packet, offset)
        if offset + 8 > len(packet):
            raise ValueError("missing Server Browser challenge")
        challenge = packet[offset : offset + 8]
        offset += 8
        filter_text, offset = _read_cstring(packet, offset)
        field_list, offset = _read_cstring(packet, offset)
        if offset + 4 > len(packet):
            raise ValueError("missing Server Browser options")
        options = struct.unpack_from(">I", packet, offset)[0]
        accepted_query_games = {self.config.game.name, f"{self.config.game.name}am"}
        if query_game not in accepted_query_games or client_game != self.config.game.name:
            LOG.warning(
                "Server Browser game mismatch source=%s query_match=%s client_match=%s",
                source_ip,
                query_game in accepted_query_games,
                client_game == self.config.game.name,
            )
            return b"Query Error: Invalid gamename or clientname\x00", None, None
        fields = _field_names(field_list)
        subscription = None
        if options & SEND_GROUPS:
            body = self._group_body(source_ip, fields)
            server_count = len(self.state.active_servers(self.config.game.name))
        else:
            servers = self._listed_servers(query_game, filter_text)
            body = self._server_body(source_ip, fields, servers)
            server_count = len(servers)
            if options & PUSH_UPDATES:
                subscription = ServerListSubscription(
                    query_game=query_game,
                    filter_text=filter_text,
                    known_servers={
                        server.source: self._server_entry(server, [], full_rules=True)
                        for server in servers
                    },
                )
        LOG.info(
            "Server Browser query source=%s fields=%d groups=%s push=%s servers=%d",
            source_ip,
            len(fields),
            bool(options & SEND_GROUPS),
            bool(options & PUSH_UPDATES),
            server_count,
        )
        response, cipher = self._encrypt_response(challenge, body)
        return response, cipher, subscription

    def _handle_info_request(self, packet: bytes, cipher: EnctypeX) -> bytes | None:
        if len(packet) != 9:
            raise ValueError("invalid Server Browser info request length")
        host = socket.inet_ntoa(packet[3:7])
        port = struct.unpack_from(">H", packet, 7)[0]
        server = next(
            (
                candidate
                for candidate in self.state.active_servers(self.config.game.name)
                if candidate.browser_endpoint() == (host, port)
            ),
            None,
        )
        LOG.info(
            "Server Browser info query host=%s port=%d found=%s",
            host,
            port,
            server is not None,
        )
        if server is None:
            return None
        payload = bytes((PUSH_SERVER_MESSAGE,)) + self._server_entry(
            server,
            [],
            full_rules=True,
        )
        frame = struct.pack(">H", len(payload) + 2) + payload
        return cipher.encrypt(frame)

    async def _handle_send_message_request(self, packet: bytes) -> None:
        if len(packet) <= 9:
            raise ValueError("invalid Server Browser send-message request length")
        host = socket.inet_ntoa(packet[3:7])
        port = struct.unpack_from(">H", packet, 7)[0]
        server = next(
            (
                candidate
                for candidate in self.state.active_servers(self.config.game.name)
                if candidate.browser_endpoint() == (host, port)
            ),
            None,
        )
        if server is None:
            raise ValueError(f"unregistered Server Browser relay target: {host}:{port}")
        payload = packet[9:]
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        relay.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_sendto(relay, payload, server.source)
        finally:
            relay.close()
        LOG.info(
            "Server Browser relayed message browser_endpoint=%s source=%s bytes=%d",
            (host, port),
            server.source,
            len(payload),
        )

    def _response_prefix(self, source_ip: str, fields: list[str], query_port: int) -> bytearray:
        output = bytearray(socket.inet_aton(source_ip))
        output.extend(struct.pack(">H", query_port))
        output.extend(struct.pack("<H", len(fields)))
        for field in fields:
            output.extend(field.encode("utf-8") + b"\x00\x00")
        return output

    def _group_body(self, source_ip: str, fields: list[str]) -> bytes:
        output = self._response_prefix(source_ip, fields, 0)
        servers = self.state.active_servers(self.config.game.name)
        values = {
            "hostname": "Fruit Ninja LAN",
            "numplayers": "0",
            "numservers": str(len(servers)),
            "numwaiting": "0",
            "maxwaiting": "2",
        }
        output.append(HAS_KEYS_FLAG)
        output.extend(struct.pack(">I", 1))
        for field in fields:
            output.append(0xFF)
            output.extend(values.get(field, "").encode("utf-8") + b"\x00")
        output.extend(b"\x00\xff\xff\xff\xff")
        return bytes(output)

    def _listed_servers(
        self,
        query_game: str,
        filter_text: str,
    ) -> list[ReportedServer]:
        servers = [
            server
            for server in self.state.active_servers(self.config.game.name)
            if server.keys.get("gamename") == query_game
            and _matches_filter(server, filter_text)
        ]
        if query_game != f"{self.config.game.name}am":
            return servers

        compatible = []
        for server in servers:
            max_players_text = server.keys.get("maxplayers", "")
            players_text = server.keys.get("numplayers", "0")
            if not max_players_text.isdecimal() or not players_text.isdecimal():
                continue
            max_players = int(max_players_text)
            players = int(players_text)
            if max_players == self.config.game.max_players and players < max_players:
                compatible.append(server)

        # Peer automatch suppresses its own listing by public IP, private IP, and
        # private port. Publishing only the oldest open host prevents two clients
        # that searched an empty list together from abandoning their rooms and
        # cross-joining each other's newly reported rooms.
        return compatible[:1]

    def _server_body(
        self,
        source_ip: str,
        fields: list[str],
        servers: list[ReportedServer],
    ) -> bytes:
        output = self._response_prefix(
            source_ip, fields, self.config.game.default_query_port
        )
        for server in servers:
            output.extend(self._server_entry(server, fields))
        output.extend(b"\x00\xff\xff\xff\xff")
        return bytes(output)

    def _subscription_frames(
        self,
        subscription: ServerListSubscription,
    ) -> list[bytes]:
        servers = self._listed_servers(
            subscription.query_game,
            subscription.filter_text,
        )
        current = {
            server.source: self._server_entry(server, [], full_rules=True)
            for server in servers
        }
        frames = []
        for source in subscription.known_servers.keys() - current.keys():
            payload = (
                bytes((DELETE_SERVER_MESSAGE,))
                + socket.inet_aton(source[0])
                + struct.pack(">H", source[1])
            )
            frames.append(struct.pack(">H", len(payload) + 2) + payload)
        for source, entry in current.items():
            if subscription.known_servers.get(source) == entry:
                continue
            payload = bytes((PUSH_SERVER_MESSAGE,)) + entry
            frames.append(struct.pack(">H", len(payload) + 2) + payload)
        subscription.known_servers = current
        return frames

    def _server_entry(
        self,
        server: ReportedServer,
        fields: list[str],
        *,
        full_rules: bool = False,
    ) -> bytes:
        if full_rules:
            flags = HAS_FULL_RULES_FLAG
        else:
            flags = HAS_KEYS_FLAG if fields else 0
        if server.keys.get("natneg", "0") != "0":
            flags |= CONNECT_NEGOTIATE_FLAG
        public_host, public_port = server.browser_endpoint()
        if public_port != self.config.game.default_query_port:
            flags |= NONSTANDARD_PORT_FLAG
        private_host = server.keys.get("localip0", "")
        try:
            private_address = socket.inet_aton(private_host) if private_host else None
        except OSError:
            private_address = None
        if private_address is not None:
            flags |= PRIVATE_IP_FLAG
        local_port_text = server.keys.get("localport", "")
        local_port = int(local_port_text) if local_port_text.isdecimal() else public_port
        if not 1 <= local_port <= 65535:
            local_port = public_port
        if local_port != self.config.game.default_query_port:
            flags |= NONSTANDARD_PRIVATE_PORT_FLAG

        output = bytearray((flags,))
        output.extend(socket.inet_aton(public_host))
        if flags & NONSTANDARD_PORT_FLAG:
            output.extend(struct.pack(">H", public_port))
        if private_address is not None:
            output.extend(private_address)
        if flags & NONSTANDARD_PRIVATE_PORT_FLAG:
            output.extend(struct.pack(">H", local_port))
        if full_rules:
            for key, value in server.keys.items():
                output.extend(key.encode("utf-8") + b"\x00")
                output.extend(value.encode("utf-8") + b"\x00")
            output.append(0)
        else:
            for field in fields:
                output.append(0xFF)
                if field == "country":
                    value = "US"
                elif field == "region":
                    value = "1"
                else:
                    value = server.keys.get(field, "")
                output.extend(value.encode("utf-8") + b"\x00")
        return bytes(output)

    def _encrypt_response(
        self,
        challenge: bytes,
        body: bytes,
    ) -> tuple[bytes, EnctypeX]:
        crypt_challenge = bytearray(secrets.token_bytes(10))
        crypt_challenge[0:2] = b"\x00\x00"
        server_challenge = secrets.token_bytes(25)
        header = (
            bytes((10 ^ 0xEC,))
            + crypt_challenge
            + bytes((25 ^ 0xEA,))
            + server_challenge
        )
        cipher = EnctypeX(
            self.config.game.secret_key,
            challenge,
            server_challenge,
        )
        return header + cipher.encrypt(body), cipher
