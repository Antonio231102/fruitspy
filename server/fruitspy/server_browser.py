from __future__ import annotations

import asyncio
import logging
import re
import secrets
import socket
import struct

from .config import ServerConfig
from .crypto import EnctypeX
from .state import ReportedServer, ServerState

LOG = logging.getLogger(__name__)
SERVER_LIST_REQUEST = 0
SERVER_INFO_REQUEST = 1
PUSH_SERVER_MESSAGE = 2
SEND_MESSAGE_REQUEST = 2
SEND_GROUPS = 32
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
        match = re.fullmatch(r"\(?\s*([A-Za-z0-9_]+)\s*=\s*'?([^')]+)'?\s*\)?", clause)
        if match is None:
            LOG.warning("unsupported Server Browser filter clause: %s", clause)
            continue
        key, expected = match.groups()
        if server.keys.get(key, "") != expected.strip():
            return False
    return True


class ServerBrowserServer:
    def __init__(self, config: ServerConfig, state: ServerState) -> None:
        self.config = config
        self.state = state

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        cipher: EnctypeX | None = None
        LOG.info("Server Browser connect peer=%s", peer)
        try:
            while True:
                header = await reader.readexactly(2)
                size = struct.unpack(">H", header)[0]
                LOG.debug("Server Browser frame peer=%s header=%s size=%d", peer, header.hex(), size)
                if not 3 <= size <= 4096:
                    raise ValueError(f"invalid Server Browser frame length: {size}")
                payload = bytearray()
                while len(payload) < size - 2:
                    try:
                        chunk = await asyncio.wait_for(reader.read(size - 2 - len(payload)), 0.1)
                    except TimeoutError:
                        break
                    if not chunk:
                        break
                    payload.extend(chunk)
                packet = header + payload
                if len(packet) != size:
                    LOG.info(
                        "Server Browser length compatibility peer=%s declared=%d received=%d",
                        peer,
                        size,
                        len(packet),
                    )
                LOG.debug("Server Browser packet peer=%s data=%s", peer, packet.hex())
                request_type = packet[2]
                if request_type == SERVER_LIST_REQUEST:
                    response, cipher = self._handle_list_request(
                        packet,
                        str(peer[0]) if peer else "127.0.0.1",
                    )
                elif request_type == SERVER_INFO_REQUEST and cipher is not None:
                    response = self._handle_info_request(packet, cipher)
                elif request_type == SEND_MESSAGE_REQUEST:
                    await self._handle_send_message_request(packet)
                    continue
                else:
                    response = None
                if response is None:
                    LOG.info("Server Browser ignored frame peer=%s data=%s", peer, packet.hex())
                else:
                    writer.write(response)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except ValueError as error:
            LOG.warning("Server Browser request rejected from %s: %s", peer, error)
        finally:
            writer.close()
            LOG.info("Server Browser disconnect peer=%s", peer)
            await writer.wait_closed()

    def handle_request(self, packet: bytes, source_ip: str) -> bytes | None:
        response, _ = self._handle_list_request(packet, source_ip)
        return response

    def _handle_list_request(
        self,
        packet: bytes,
        source_ip: str,
    ) -> tuple[bytes | None, EnctypeX | None]:
        if len(packet) < 5 or packet[2] != SERVER_LIST_REQUEST:
            return None, None
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
                "Server Browser game mismatch source=%s query=%s client=%s",
                source_ip,
                query_game,
                client_game,
            )
            return b"Query Error: Invalid gamename or clientname\x00", None
        fields = _field_names(field_list)
        if options & SEND_GROUPS:
            body = self._group_body(source_ip, fields)
        else:
            body = self._server_body(source_ip, fields, filter_text)
        LOG.info(
            "Server Browser query source=%s fields=%d groups=%s servers=%d",
            source_ip,
            len(fields),
            bool(options & SEND_GROUPS),
            len(self.state.active_servers(self.config.game.name)),
        )
        return self._encrypt_response(challenge, body)

    def _handle_info_request(self, packet: bytes, cipher: EnctypeX) -> bytes | None:
        if len(packet) != 9:
            raise ValueError("invalid Server Browser info request length")
        host = socket.inet_ntoa(packet[3:7])
        port = struct.unpack_from(">H", packet, 7)[0]
        server = next(
            (
                candidate
                for candidate in self.state.active_servers(self.config.game.name)
                if candidate.public_host == host and candidate.public_port == port
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
                if candidate.public_host == host and candidate.public_port == port
            ),
            None,
        )
        if server is None:
            raise ValueError(f"unregistered Server Browser relay target: {host}:{port}")
        payload = packet[9:]
        relay = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        relay.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_sendto(relay, payload, (host, port))
        finally:
            relay.close()
        LOG.info(
            "Server Browser relayed message host=%s port=%d bytes=%d",
            host,
            port,
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

    def _server_body(self, source_ip: str, fields: list[str], filter_text: str) -> bytes:
        output = self._response_prefix(
            source_ip, fields, self.config.game.default_query_port
        )
        for server in self.state.active_servers(self.config.game.name):
            if _matches_filter(server, filter_text):
                output.extend(self._server_entry(server, fields))
        output.extend(b"\x00\xff\xff\xff\xff")
        return bytes(output)

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
        public_port = server.public_port
        if public_port != self.config.game.default_query_port:
            flags |= NONSTANDARD_PORT_FLAG
        private_host = server.keys.get("localip0", "")
        if private_host:
            flags |= PRIVATE_IP_FLAG
        local_port_text = server.keys.get("localport", "")
        local_port = int(local_port_text) if local_port_text.isdecimal() else public_port
        if local_port != self.config.game.default_query_port:
            flags |= NONSTANDARD_PRIVATE_PORT_FLAG

        output = bytearray((flags,))
        output.extend(socket.inet_aton(server.public_host))
        if flags & NONSTANDARD_PORT_FLAG:
            output.extend(struct.pack(">H", public_port))
        if flags & PRIVATE_IP_FLAG:
            output.extend(socket.inet_aton(private_host))
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
