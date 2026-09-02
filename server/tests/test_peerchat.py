import asyncio
import unittest

from fruitspy.crypto import PeerChatCipher
from fruitspy.peerchat import PeerChatServer
from tests.helpers import test_config


class EncryptedPeerClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        outgoing: PeerChatCipher,
        incoming: PeerChatCipher,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.outgoing = outgoing
        self.incoming = incoming
        self.buffer = bytearray()

    @classmethod
    async def connect(cls, port: int) -> "EncryptedPeerClient":
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CRYPT des 1 FruitNinjaand\r\n")
        await writer.drain()
        response = (await reader.readline()).decode("ascii")
        fields = response.strip().split()
        if len(fields) < 5 or fields[1] != "705":
            raise AssertionError(f"unexpected CRYPT response: {response!r}")
        client_key, server_key = fields[-2:]
        return cls(
            reader,
            writer,
            PeerChatCipher(client_key, "nNfhSl"),
            PeerChatCipher(server_key, "nNfhSl"),
        )

    async def send(self, line: str) -> None:
        self.writer.write(self.outgoing.transform((line + "\r\n").encode("ascii")))
        await self.writer.drain()

    async def read_until(self, marker: str) -> str:
        needle = marker.encode("ascii")
        async with asyncio.timeout(2):
            while needle not in self.buffer:
                chunk = await self.reader.read(4096)
                if not chunk:
                    raise AssertionError("PeerChat connection closed")
                self.buffer.extend(self.incoming.transform(chunk))
        output = self.buffer.decode("utf-8", "replace")
        self.buffer.clear()
        return output

    async def close(self) -> None:
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 1)
        except (TimeoutError, ConnectionError):
            self.writer.transport.abort()


class PeerChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = PeerChatServer(test_config())
        self.listener = await asyncio.start_server(
            self.service.handle,
            "127.0.0.1",
            0,
        )
        self.port = self.listener.sockets[0].getsockname()[1]

    async def asyncTearDown(self) -> None:
        self.listener.close()
        await self.listener.wait_closed()

    async def test_usrip_reports_observed_address_in_gamespy_format(self) -> None:
        client = await EncryptedPeerClient.connect(self.port)
        try:
            await client.send("USRIP")
            response = await client.read_until("\r\n")
            self.assertEqual(response, ":s 302 :+@127.0.0.1\r\n")
        finally:
            await client.close()

    async def test_encrypted_two_user_staging_room(self) -> None:
        first = await EncryptedPeerClient.connect(self.port)
        second = await EncryptedPeerClient.connect(self.port)
        try:
            await first.send("NICK player1")
            await first.send("USER player1 0 * :Player One")
            await first.read_until("376 player1")
            await second.send("NICK player2")
            await second.send("USER player2 0 * :Player Two")
            await second.read_until("376 player2")

            channel = "#GSP!FruitNinjaand!room"
            await first.send(f"JOIN {channel}")
            await first.read_until("End of NAMES list")
            await second.send(f"JOIN {channel}")
            names = await second.read_until("End of NAMES list")
            self.assertIn(f"{channel} :@player1 player2", names)
            await first.read_until("player2!player2@")
            await first.send(f"GETCKEY {channel} * 001 0 :\\username\\b_flags")
            keys = await first.read_until("End of GETCKEY")
            self.assertIn(f"{channel} player2 001 :\\player2\\", keys)


            await first.send(f"SETCKEY {channel} player1 :\\b_flags\\s")
            await second.read_until("BCAST")
            await first.send(f"PRIVMSG {channel} :ready")
            message = await second.read_until("PRIVMSG")
            self.assertIn(":ready", message)
            self.assertEqual(len(self.service.channels[channel.casefold()].users), 2)
        finally:
            await first.close()
            await second.close()


if __name__ == "__main__":
    unittest.main()
