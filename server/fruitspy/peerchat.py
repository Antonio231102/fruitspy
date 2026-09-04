from __future__ import annotations

import asyncio
import logging
import secrets
import string
from dataclasses import dataclass, field

from .admission import ConnectionAdmission
from .config import ServerConfig
from .crypto import PeerChatCipher

LOG = logging.getLogger(__name__)


def _key_pairs(value: str) -> dict[str, str]:
    parts = value.lstrip("\\").split("\\") if value else []
    result: dict[str, str] = {}
    for index in range(0, len(parts), 2):
        if not parts[index]:
            continue
        result[parts[index]] = parts[index + 1] if index + 1 < len(parts) else ""
    return result


def _requested_keys(value: str) -> list[str]:
    return [part for part in value.lstrip("\\").split("\\") if part]


@dataclass(slots=True)
class ChatChannel:
    name: str
    users: dict[str, "PeerChatClient"] = field(default_factory=dict)
    keys: dict[str, str] = field(default_factory=dict)
    client_keys: dict[str, dict[str, str]] = field(default_factory=dict)
    topic: str = ""
    operators: set[str] = field(default_factory=set)


class PeerChatServer:
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.clients: dict[str, PeerChatClient] = {}
        self.channels: dict[str, ChatChannel] = {}
        self.admission = ConnectionAdmission(
            config.limits.peerchat_connections,
            config.limits.connections_per_source,
        )

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = PeerChatClient(self, reader, writer)
        if not self.admission.acquire(client.host):
            LOG.warning(
                "service=peerchat event=admission_rejected source=%s",
                client.host,
            )
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (TimeoutError, ConnectionError):
                writer.transport.abort()
            return
        LOG.info(
            "service=peerchat event=connected connection=%s source=%s",
            client.connection_id,
            client.host,
        )
        try:
            await client.run()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except ValueError as error:
            LOG.warning(
                "service=peerchat event=request_rejected connection=%s source=%s error=%s",
                client.connection_id,
                client.host,
                error,
            )
        finally:
            try:
                await client.disconnect("Client exited")
            finally:
                self.admission.release(client.host)

    def channel(self, name: str) -> ChatChannel:
        folded = name.casefold()
        channel = self.channels.get(folded)
        if channel is None:
            channel = ChatChannel(name=name)
            self.channels[folded] = channel
        return channel


class PeerChatClient:
    def __init__(
        self,
        server: PeerChatServer,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.server = server
        self.reader = reader
        self.writer = writer
        peer = writer.get_extra_info("peername")
        self.host = str(peer[0]) if peer else "0.0.0.0"
        self.nick = ""
        self.user = ""
        self.realname = ""
        self.welcomed = False
        self.closed = False
        self.user_keys: dict[str, str] = {}
        self.channels: set[str] = set()
        self.decryptor: PeerChatCipher | None = None
        self.encryptor: PeerChatCipher | None = None
        self._plain_buffer = bytearray()
        self.connection_id = secrets.token_hex(8)
        self._handshake_deadline = (
            asyncio.get_running_loop().time()
            + server.config.timeouts.peerchat_handshake_seconds
        )

    @property
    def prefix(self) -> str:
        return f"{self.nick or '*'}!{self.user or 'user'}@{self.host}"

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        while not self.reader.at_eof():
            if self.welcomed:
                data = await self.reader.read(4096)
            else:
                timeout = self._handshake_deadline - loop.time()
                if timeout <= 0:
                    raise ValueError("PeerChat handshake deadline exceeded")
                try:
                    data = await asyncio.wait_for(self.reader.read(4096), timeout)
                except TimeoutError as error:
                    raise ValueError("PeerChat handshake deadline exceeded") from error
            if not data:
                return
            if self.decryptor is not None:
                data = self.decryptor.transform(data)
            self._plain_buffer.extend(data)
            while b"\n" in self._plain_buffer:
                raw, _, remaining = self._plain_buffer.partition(b"\n")
                self._plain_buffer = bytearray(remaining)
                if len(raw) > self.server.config.limits.peerchat_line_bytes:
                    raise ValueError("PeerChat line exceeds configured limit")
                line = raw.rstrip(b"\r").decode("utf-8", "replace")
                if line:
                    await self.command(line)
            if len(self._plain_buffer) > self.server.config.limits.peerchat_line_bytes:
                raise ValueError("PeerChat line exceeds configured limit")

    async def send(self, line: str, *, plaintext: bool = False) -> None:
        if self.closed:
            return
        data = (line + "\r\n").encode("utf-8")
        if self.server.config.mode == "internet":
            LOG.debug(
                "service=peerchat event=send connection=%s bytes=%d",
                self.connection_id,
                len(data),
            )
        else:
            LOG.debug(
                "PeerChat S->C host=%s nick=%s line=%s",
                self.host,
                self.nick or "*",
                line,
            )
        if self.encryptor is not None and not plaintext:
            data = self.encryptor.transform(data)
        self.writer.write(data)
        await self.writer.drain()

    async def numeric(self, code: int, text: str, *, plaintext: bool = False) -> None:
        await self.send(f":s {code:03d} {self.nick or '*'} {text}", plaintext=plaintext)

    async def command(self, line: str) -> None:
        command, _, params = line.partition(" ")
        name = command.upper()
        if self.server.config.mode == "internet":
            LOG.debug(
                "service=peerchat event=receive connection=%s command=%s bytes=%d",
                self.connection_id,
                name,
                len(line.encode("utf-8")),
            )
        else:
            LOG.debug(
                "PeerChat C->S host=%s nick=%s line=%s",
                self.host,
                self.nick or "*",
                line,
            )
        handler = getattr(self, f"cmd_{name.lower()}", None)
        if handler is None:
            await self.numeric(421, f"{name} :Unknown command")
            return
        await handler(params)

    async def cmd_crypt(self, params: str) -> None:
        parts = params.split()
        if len(parts) < 3 or parts[-1] != self.server.config.game.name:
            await self.send("ERROR :Invalid Game", plaintext=True)
            return
        alphabet = string.ascii_letters + string.digits
        client_key = "".join(secrets.choice(alphabet) for _ in range(16))
        server_key = "".join(secrets.choice(alphabet) for _ in range(16))
        await self.numeric(705, f"{client_key} {server_key}", plaintext=True)
        secret = self.server.config.game.secret_key
        self.decryptor = PeerChatCipher(client_key, secret)
        self.encryptor = PeerChatCipher(server_key, secret)

    async def cmd_nick(self, params: str) -> None:
        new_nick = params.lstrip(":").strip()
        if not new_nick or len(new_nick) > 31:
            await self.numeric(432, f"{new_nick} :Erroneous nickname")
            return
        existing = self.server.clients.get(new_nick.casefold())
        if existing is not None and existing is not self:
            await self.numeric(433, f"{new_nick} :Nickname is already in use")
            return
        old_nick = self.nick
        if old_nick:
            self.server.clients.pop(old_nick.casefold(), None)
        self.nick = new_nick
        self.server.clients[new_nick.casefold()] = self
        if old_nick:
            for channel_name in list(self.channels):
                channel = self.server.channels[channel_name]
                channel.users.pop(old_nick.casefold(), None)
                channel.users[new_nick.casefold()] = self
                keys = channel.client_keys.pop(old_nick.casefold(), {})
                channel.client_keys[new_nick.casefold()] = keys
                if old_nick.casefold() in channel.operators:
                    channel.operators.remove(old_nick.casefold())
                    channel.operators.add(new_nick.casefold())
                await self._broadcast(channel, f":{old_nick}!{self.user}@{self.host} NICK :{new_nick}")
        await self._welcome()

    async def cmd_user(self, params: str) -> None:
        fields = params.split(" ", 3)
        if not fields:
            await self.numeric(461, "USER :Not enough parameters")
            return
        self.user = fields[0]
        self.user_keys["username"] = self.user
        self.realname = fields[3].lstrip(":") if len(fields) > 3 else self.user
        await self._welcome()

    async def _welcome(self) -> None:
        if self.welcomed or not self.nick or not self.user:
            return
        self.welcomed = True
        await self.numeric(1, f":Welcome to FruitSpy {self.nick}")
        await self.numeric(2, ":Your host is FruitSpy, running version 0.1")
        await self.numeric(3, ":This server implements the GameSpy PeerChat protocol")
        await self.numeric(4, "s 0.1 iq biklmnopqustvhe")
        await self.numeric(375, ":- Message of the day -")
        await self.numeric(372, ":- Fruit Ninja LAN multiplayer service")
        await self.numeric(376, ":End of MOTD command")

    async def cmd_ping(self, params: str) -> None:
        await self.send(f"PONG :{params.lstrip(':')}")

    async def cmd_pong(self, params: str) -> None:
        return

    async def cmd_usrip(self, params: str) -> None:
        await self.send(f":s 302 :+@{self.host}")

    async def cmd_cdkey(self, params: str) -> None:
        await self.numeric(706, "1 :Authenticated")

    async def cmd_join(self, params: str) -> None:
        channel_name = params.split()[0] if params else ""
        if not channel_name:
            await self.numeric(461, "JOIN :Not enough parameters")
            return
        channel = self.server.channel(channel_name)
        folded_nick = self.nick.casefold()
        first = not channel.users
        channel.users[folded_nick] = self
        channel.client_keys.setdefault(folded_nick, {})
        if first:
            channel.operators.add(folded_nick)
        folded_channel = channel.name.casefold()
        self.channels.add(folded_channel)
        await self._broadcast(channel, f":{self.prefix} JOIN :{channel.name}")
        if first:
            await self._broadcast(channel, f":s MODE {channel.name} +o {self.nick}")
        if channel.topic:
            await self.numeric(332, f"{channel.name} :{channel.topic}")
        names = " ".join(
            f"@{client.nick}" if folded in channel.operators else client.nick
            for folded, client in channel.users.items()
        )
        await self.numeric(353, f"= {channel.name} :{names}")
        await self.numeric(366, f"{channel.name} :End of NAMES list")

    async def cmd_part(self, params: str) -> None:
        channel_name, _, reason = params.partition(" ")
        await self._leave_channel(channel_name, reason.lstrip(":") or "Leaving")

    async def _leave_channel(self, channel_name: str, reason: str) -> None:
        channel = self.server.channels.get(channel_name.casefold())
        if channel is None or self.nick.casefold() not in channel.users:
            return
        await self._broadcast(channel, f":{self.prefix} PART {channel.name} :{reason}")
        channel.users.pop(self.nick.casefold(), None)
        channel.client_keys.pop(self.nick.casefold(), None)
        channel.operators.discard(self.nick.casefold())
        self.channels.discard(channel.name.casefold())
        if not channel.users:
            self.server.channels.pop(channel.name.casefold(), None)

    async def cmd_names(self, params: str) -> None:
        channel = self.server.channels.get(params.strip().casefold())
        if channel is None:
            await self.numeric(366, f"{params.strip()} :End of NAMES list")
            return
        names = " ".join(
            f"@{client.nick}" if folded in channel.operators else client.nick
            for folded, client in channel.users.items()
        )
        await self.numeric(353, f"= {channel.name} :{names}")
        await self.numeric(366, f"{channel.name} :End of NAMES list")

    async def cmd_who(self, params: str) -> None:
        target = params.split()[0] if params else ""
        channel = self.server.channels.get(target.casefold())
        if channel is not None:
            for client in channel.users.values():
                await self.numeric(
                    352,
                    f"{channel.name} {client.user} {client.host} s {client.nick} H :0 {client.realname}",
                )
        else:
            client = self.server.clients.get(target.casefold())
            if client is not None:
                await self.numeric(352, f"* {client.user} {client.host} s {client.nick} H :0 {client.realname}")
        await self.numeric(315, f"{target} :End of WHO list")

    async def cmd_privmsg(self, params: str) -> None:
        await self._message("PRIVMSG", params)

    async def cmd_notice(self, params: str) -> None:
        await self._message("NOTICE", params)

    async def cmd_utm(self, params: str) -> None:
        await self._message("UTM", params)

    async def cmd_atm(self, params: str) -> None:
        await self._message("ATM", params)

    async def _message(self, command: str, params: str) -> None:
        target, _, message = params.partition(" ")
        line = f":{self.prefix} {command} {target} {message}"
        channel = self.server.channels.get(target.casefold())
        if channel is not None:
            await self._broadcast(channel, line, exclude=self)
            return
        client = self.server.clients.get(target.casefold())
        if client is not None:
            await client.send(line)

    async def cmd_setkey(self, params: str) -> None:
        _, _, value = params.partition(":")
        self.user_keys.update(_key_pairs(value))

    async def cmd_getkey(self, params: str) -> None:
        before, _, query = params.partition(":")
        fields = before.split()
        if len(fields) < 2:
            await self.numeric(461, "GETKEY :Not enough parameters")
            return
        target = self.server.clients.get(fields[0].casefold())
        if target is None:
            await self.numeric(401, f"{fields[0]} :No such nick/channel")
            return
        cookie = fields[1]
        values = "".join(f"\\{target.user_keys.get(key, '')}" for key in _requested_keys(query))
        await self.send(f":s 700 {self.nick} {target.nick} {cookie} :{values}")

    async def cmd_setchankey(self, params: str) -> None:
        channel_name, _, value = params.partition(":")
        channel = self.server.channels.get(channel_name.strip().casefold())
        if channel is not None:
            channel.keys.update(_key_pairs(value))

    async def cmd_getchankey(self, params: str) -> None:
        before, _, query = params.partition(":")
        fields = before.split()
        if len(fields) < 2:
            await self.numeric(461, "GETCHANKEY :Not enough parameters")
            return
        channel = self.server.channels.get(fields[0].casefold())
        if channel is None:
            await self.numeric(401, f"{fields[0]} :No such nick/channel")
            return
        cookie = fields[1]
        keys = _requested_keys(query) or list(channel.keys)
        values = "".join(f"\\{channel.keys.get(key, '')}" for key in keys)
        await self.send(f":s 704 {self.nick} {channel.name} {cookie} :{values}")

    async def cmd_setckey(self, params: str) -> None:
        before, _, value = params.partition(":")
        fields = before.split()
        if len(fields) < 2:
            await self.numeric(461, "SETCKEY :Not enough parameters")
            return
        channel = self.server.channels.get(fields[0].casefold())
        target = self.server.clients.get(fields[1].casefold())
        if channel is None or target is None:
            return
        channel.client_keys.setdefault(target.nick.casefold(), {}).update(_key_pairs(value))
        await self._broadcast(
            channel,
            f":s 702 {channel.name} {channel.name} {target.nick} BCAST :{value}",
        )

    async def cmd_getckey(self, params: str) -> None:
        before, _, query = params.partition(":")
        fields = before.split()
        if len(fields) < 4:
            await self.numeric(461, "GETCKEY :Not enough parameters")
            return
        channel = self.server.channels.get(fields[0].casefold())
        if channel is None:
            return
        target_name = fields[1]
        cookie = fields[2]
        keys = _requested_keys(query)
        clients = (
            list(channel.users.values())
            if target_name == "*"
            else [channel.users.get(target_name.casefold())]
        )
        for client in clients:
            if client is None:
                continue
            ckeys = channel.client_keys.get(client.nick.casefold(), {})
            values = "".join(
                f"\\{client.user_keys.get(key, client.user if key == 'username' else ckeys.get(key, ''))}"
                for key in keys
            )
            await self.send(
                f":s 702 {self.nick} {channel.name} {client.nick} {cookie} :{values}"
            )
        await self.numeric(703, f"{channel.name} {cookie} :End of GETCKEY")

    async def cmd_mode(self, params: str) -> None:
        fields = params.split()
        if not fields:
            return
        channel = self.server.channels.get(fields[0].casefold())
        if channel is None:
            return
        if len(fields) == 1:
            await self.numeric(324, f"{channel.name} +nt")
        else:
            if len(fields) >= 3 and fields[1] in {"+o", "-o"}:
                operator = fields[2].casefold()
                if fields[1] == "+o":
                    channel.operators.add(operator)
                else:
                    channel.operators.discard(operator)
            await self._broadcast(channel, f":{self.prefix} MODE {params}")

    async def cmd_topic(self, params: str) -> None:
        channel_name, _, topic = params.partition(" :")
        channel = self.server.channels.get(channel_name.casefold())
        if channel is None:
            return
        if topic:
            channel.topic = topic
            await self._broadcast(channel, f":{self.prefix} TOPIC {channel.name} :{topic}")
        else:
            await self.numeric(332, f"{channel.name} :{channel.topic}")

    async def cmd_list(self, params: str) -> None:
        await self.numeric(321, "Channel :Users Name")
        for channel in self.server.channels.values():
            await self.numeric(322, f"{channel.name} {len(channel.users)} :{channel.topic}")
        await self.numeric(323, ":End of LIST")

    async def cmd_quit(self, params: str) -> None:
        await self.disconnect(params.lstrip(":") or "Client exited")

    async def _broadcast(
        self,
        channel: ChatChannel,
        line: str,
        exclude: "PeerChatClient | None" = None,
    ) -> None:
        for client in list(channel.users.values()):
            if client is not exclude:
                await client.send(line)

    async def disconnect(self, reason: str) -> None:
        if self.closed:
            return
        self.closed = True
        for channel_name in list(self.channels):
            channel = self.server.channels.get(channel_name)
            if channel is None:
                continue
            for client in list(channel.users.values()):
                if client is not self:
                    await client.send(f":{self.prefix} QUIT :{reason}")
            channel.users.pop(self.nick.casefold(), None)
            channel.client_keys.pop(self.nick.casefold(), None)
            channel.operators.discard(self.nick.casefold())
            if not channel.users:
                self.server.channels.pop(channel_name, None)
        if self.nick:
            self.server.clients.pop(self.nick.casefold(), None)
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 1)
        except (TimeoutError, ConnectionError):
            self.writer.transport.abort()
        if self.server.config.mode == "internet":
            LOG.info(
                "service=peerchat event=disconnected connection=%s source=%s",
                self.connection_id,
                self.host,
            )
        else:
            LOG.info(
                "PeerChat disconnect nick=%s host=%s reason=%s",
                self.nick,
                self.host,
                reason,
            )
