import asyncio
import unittest
from dataclasses import replace

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

    async def connect_player(self, nick: str) -> EncryptedPeerClient:
        client = await EncryptedPeerClient.connect(self.port)
        await client.send(f"NICK {nick}")
        await client.send(f"USER {nick} 0 * :{nick}")
        await client.read_until(f"376 {nick}")
        return client

    async def test_usrip_reports_observed_address_in_gamespy_format(self) -> None:
        client = await EncryptedPeerClient.connect(self.port)
        try:
            await client.send("USRIP")
            response = await client.read_until("\r\n")
            self.assertEqual(response, ":s 302 :+@127.0.0.1\r\n")
        finally:
            await client.close()

    async def test_cdkey_is_not_treated_as_authentication(self) -> None:
        client = await EncryptedPeerClient.connect(self.port)
        try:
            await client.send("CDKEY unused")
            response = await client.read_until("\r\n")
            self.assertEqual(response, ":s 421 * CDKEY :Unknown command\r\n")
        finally:
            await client.close()

    async def test_external_command_log_field_is_sanitized(self) -> None:
        client = await EncryptedPeerClient.connect(self.port)
        try:
            with self.assertLogs("fruitspy.peerchat", level="DEBUG") as captured:
                await client.send("BAD\x1bFORGED")
                await client.read_until("Unknown command")
            output = "\n".join(captured.output)
            self.assertNotIn("\x1b", output)
            self.assertIn(r"command=BAD\x1bFORGED", output)
        finally:
            await client.close()

    async def test_metrics_track_clients_and_rooms_without_names(self) -> None:
        client = await self.connect_player("private-player")
        try:
            await client.send("JOIN #private-room")
            await client.read_until("End of NAMES list")
            metrics = self.service.metrics.render().decode("utf-8")
            self.assertIn("fruitspy_peerchat_clients 1", metrics)
            self.assertIn("fruitspy_peerchat_rooms 1", metrics)
            self.assertNotIn("private-player", metrics)
            self.assertNotIn("private-room", metrics)

            await client.send("PART #private-room :Leaving")
            await client.read_until("PART #private-room")
            metrics = self.service.metrics.render().decode("utf-8")
            self.assertIn("fruitspy_peerchat_rooms 0", metrics)
        finally:
            await client.close()

    async def test_welcomed_connection_remains_open_without_traffic(self) -> None:
        client = await EncryptedPeerClient.connect(self.port)
        try:
            await client.send("NICK player")
            await client.send("USER player 0 * :Player")
            await client.read_until("376 player")

            await asyncio.sleep(1.1)

            await client.send("PING keepalive")
            response = await client.read_until("\r\n")
            self.assertIn("PONG :keepalive", response)
        finally:
            await client.close()

    async def test_oversized_line_is_rejected(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        try:
            writer.write(b"x" * (self.service.config.limits.peerchat_line_bytes + 1))
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(1), 2), b"")
        finally:
            writer.close()
            await writer.wait_closed()

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

    async def test_channel_limits_reject_without_creating_membership(self) -> None:
        self.service.config = replace(
            self.service.config,
            limits=replace(
                self.service.config.limits,
                peerchat_channels=2,
                peerchat_channels_per_client=1,
            ),
        )
        first = await self.connect_player("player1")
        second = await self.connect_player("player2")
        third = await self.connect_player("player3")
        try:
            await first.send("JOIN #one")
            await first.read_until("End of NAMES list")

            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await first.send("JOIN #two")
                per_client = await first.read_until("You have joined too many channels")
            self.assertIn("405 player1 #two", per_client)
            self.assertNotIn("#two", self.service.channels)
            self.assertNotIn("#two", self.service.clients["player1"].channels)
            self.assertIn("resource=channels_per_client current=1 limit=1", "\n".join(captured.output))

            await second.send("JOIN #two")
            await second.read_until("End of NAMES list")
            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await third.send("JOIN #three")
                global_limit = await third.read_until("You have joined too many channels")
            self.assertIn("405 player3 #three", global_limit)
            self.assertNotIn("#three", self.service.channels)
            self.assertIn("resource=channels current=2 limit=2", "\n".join(captured.output))

            await first.send("PART #one :Leaving")
            await first.read_until("PART #one")
            await third.send("JOIN #three")
            await third.read_until("End of NAMES list")
            self.assertIn("#three", self.service.channels)
            self.assertIn("#three", self.service.clients["player3"].channels)
        finally:
            await first.close()
            await second.close()
            await third.close()

    async def test_all_peerchat_key_collections_are_bounded_atomically(self) -> None:
        self.service.config = replace(
            self.service.config,
            limits=replace(
                self.service.config.limits,
                peerchat_keys_per_collection=2,
            ),
        )
        first = await self.connect_player("player1")
        outsider = await self.connect_player("player2")
        try:
            await first.send("SETKEY :\\alpha\\1")
            await first.send("PING user-key-barrier")
            await first.read_until("PONG :user-key-barrier")
            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await first.send("SETKEY :\\alpha\\changed\\beta\\2")
                await first.read_until("SETKEY :Resource limit exceeded")
            first_keys = self.service.clients["player1"].user_keys
            self.assertEqual(first_keys, {"username": "player1", "alpha": "1"})
            self.assertNotIn("beta", first_keys)
            output = "\n".join(captured.output)
            self.assertIn(
                "resource=user_keys current=2 requested_new=1 limit=2",
                output,
            )
            await first.send("SETKEY :\\alpha\\updated")
            await first.send("PING user-key-update-barrier")
            await first.read_until("PONG :user-key-update-barrier")
            self.assertEqual(first_keys["alpha"], "updated")

            channel_name = "#keys"
            await first.send(f"JOIN {channel_name}")
            await first.read_until("End of NAMES list")
            channel = self.service.channels[channel_name]
            await first.send(f"SETCHANKEY {channel_name} :\\one\\1\\two\\2")
            await first.send("PING channel-key-barrier")
            await first.read_until("PONG :channel-key-barrier")
            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await first.send(
                    f"SETCHANKEY {channel_name} :\\one\\changed\\three\\3"
                )
                await first.read_until("SETCHANKEY :Resource limit exceeded")
            self.assertEqual(channel.keys, {"one": "1", "two": "2"})
            output = "\n".join(captured.output)
            self.assertIn(
                "resource=channel_keys current=2 requested_new=1 limit=2",
                output,
            )

            await first.send(f"SETCKEY {channel_name} player1 :\\one\\1\\two\\2")
            await first.read_until("BCAST")
            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await first.send(
                    f"SETCKEY {channel_name} player1 :\\one\\changed\\three\\3"
                )
                await first.read_until("SETCKEY :Resource limit exceeded")
            self.assertEqual(
                channel.client_keys["player1"],
                {"one": "1", "two": "2"},
            )
            output = "\n".join(captured.output)
            self.assertIn(
                "resource=client_keys current=2 requested_new=1 limit=2",
                output,
            )

            await first.send(f"SETCKEY {channel_name} player2 :\\outside\\1")
            await first.send("PING client-key-barrier")
            await first.read_until("PONG :client-key-barrier")
            self.assertNotIn("player2", channel.client_keys)
            await first.send(f"MODE {channel_name} +o player2")
            mode_rejection = await first.read_until("They aren't on that channel")
            self.assertIn(f"441 player1 player2 {channel_name}", mode_rejection)
            self.assertNotIn("player2", channel.operators)
        finally:
            await first.close()
            await outsider.close()

    async def test_command_budget_disconnects_flooding_client(self) -> None:
        self.service.config = replace(
            self.service.config,
            limits=replace(
                self.service.config.limits,
                peerchat_commands_per_second=1,
                peerchat_command_burst=5,
            ),
        )
        client = await self.connect_player("player1")
        try:
            await client.send("PING one")
            await client.read_until("PONG :one")
            await client.send("PING two")
            await client.read_until("PONG :two")

            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await client.send("PING three")
                rejection = await client.read_until("Command budget exceeded")

            self.assertIn("263 player1 PING :Command budget exceeded", rejection)
            self.assertIn(
                "event=client_budget_exhausted",
                "\n".join(captured.output),
            )
            self.assertIn("budget=commands command=PING", "\n".join(captured.output))
            self.assertEqual(
                await asyncio.wait_for(client.reader.read(1), 2),
                b"",
            )
            self.assertNotIn("player1", self.service.clients)
        finally:
            await client.close()

    async def test_state_creation_budget_rejects_without_partial_state(self) -> None:
        self.service.config = replace(
            self.service.config,
            limits=replace(
                self.service.config.limits,
                peerchat_state_creations_per_second=1,
                peerchat_state_creation_burst=4,
            ),
        )
        client = await self.connect_player("player1")
        try:
            await client.send("JOIN #budget")
            await client.read_until("End of NAMES list")
            await client.send("SETKEY :\\one\\1")
            await client.send("PING initial-state-barrier")
            await client.read_until("PONG :initial-state-barrier")
            await client.send("PART #budget :Leaving")
            await client.read_until("PART #budget")
            budget = self.service.clients["player1"].state_creation_budget
            budget.tokens = 0
            budget.updated_at = budget.clock()

            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await client.send("JOIN #rejected")
                join_rejection = await client.read_until(
                    "JOIN :State creation budget exceeded"
                )

            self.assertIn("263 player1", join_rejection)
            self.assertNotIn("#rejected", self.service.channels)
            self.assertNotIn(
                "#rejected",
                self.service.clients["player1"].channels,
            )
            output = "\n".join(captured.output)
            self.assertIn("budget=state_creation command=JOIN cost=2", output)

            budget.tokens = 0
            budget.updated_at = budget.clock()
            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await client.send("SETKEY :\\one\\changed\\two\\2")
                key_rejection = await client.read_until(
                    "SETKEY :State creation budget exceeded"
                )
            self.assertIn("263 player1", key_rejection)
            self.assertEqual(
                self.service.clients["player1"].user_keys,
                {"username": "player1", "one": "1"},
            )
            self.assertIn(
                "budget=state_creation command=SETKEY cost=1",
                "\n".join(captured.output),
            )

            await client.send("SETKEY :\\one\\updated")
            await client.send("PING existing-state-update")
            response = await client.read_until("PONG :existing-state-update")
            self.assertIn("PONG :existing-state-update", response)
            self.assertEqual(
                self.service.clients["player1"].user_keys["one"],
                "updated",
            )
        finally:
            await client.close()

    async def test_third_staging_room_participant_is_rejected(self) -> None:
        first = await self.connect_player("player1")
        second = await self.connect_player("player2")
        third = await self.connect_player("player3")
        try:
            channel_name = "#GSP!FruitNinjaand!limited\x1bFORGED"
            await first.send(f"JOIN {channel_name}")
            await first.read_until("End of NAMES list")
            await second.send(f"JOIN {channel_name}")
            await second.read_until("End of NAMES list")
            await first.read_until("player2!player2@")

            with self.assertLogs("fruitspy.peerchat", level="WARNING") as captured:
                await third.send(f"JOIN {channel_name}")
                rejection = await third.read_until("Cannot join channel (+l)")

            self.assertIn(f"471 player3 {channel_name}", rejection)
            channel = self.service.channels[channel_name.casefold()]
            self.assertEqual(set(channel.users), {"player1", "player2"})
            self.assertNotIn(channel_name.casefold(), self.service.clients["player3"].channels)
            output = "\n".join(captured.output)
            self.assertIn("event=room_join_rejected", output)
            self.assertIn("reason=participant_limit participants=2 limit=2", output)
            self.assertNotIn("\x1b", output)
            self.assertIn(
                r"channel=#GSP!FruitNinjaand!limited\x1bFORGED",
                output,
            )

            await second.send(f"PART {channel_name} :Leaving")
            await first.read_until(f"PART {channel_name}")
            await third.send(f"JOIN {channel_name}")
            names = await third.read_until("End of NAMES list")

            self.assertIn(f"{channel_name} :@player1 player3", names)
            self.assertEqual(set(channel.users), {"player1", "player3"})
        finally:
            await first.close()
            await second.close()
            await third.close()

    async def test_title_room_remains_unbounded_by_staging_limit(self) -> None:
        clients = [
            await self.connect_player("player1"),
            await self.connect_player("player2"),
            await self.connect_player("player3"),
        ]
        try:
            channel_name = "#GSP!FruitNinjaand"
            for client in clients:
                await client.send(f"JOIN {channel_name}")
                await client.read_until("End of NAMES list")

            channel = self.service.channels[channel_name.casefold()]
            self.assertIsNone(channel.participant_limit)
            self.assertEqual(set(channel.users), {"player1", "player2", "player3"})
        finally:
            for client in clients:
                await client.close()


if __name__ == "__main__":
    unittest.main()
