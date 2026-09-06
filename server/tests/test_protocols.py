import socket
import struct
import unittest

from fruitspy.availability_qr import AvailabilityQRProtocol
from fruitspy.crypto import EnctypeX, gsseckey
from fruitspy.natneg import (
    MAGIC,
    NN_CONNECT,
    NN_INIT,
    NN_INIT_ACK,
    NN_REPORT,
    NN_REPORT_ACK,
    NatNegProtocol,
)
from fruitspy.server_browser import ServerBrowserServer
from fruitspy.state import ServerState
from tests.helpers import test_config


def server_browser_request(
    challenge: bytes,
    fields: str,
    options: int = 0,
    query_game: str = "FruitNinjaand",
) -> bytes:
    payload = bytearray((1, 3))
    payload.extend(struct.pack(">I", 1))
    payload.extend(query_game.encode("ascii") + b"\x00FruitNinjaand\x00")
    payload.extend(challenge)
    payload.extend(b"\x00")
    payload.extend(fields.encode("ascii") + b"\x00")
    payload.extend(struct.pack(">I", options))
    size = len(payload) + 3
    return struct.pack(">H", size) + b"\x00" + payload


def decrypt_server_browser_stream(
    response: bytes,
    challenge: bytes,
) -> tuple[bytes, EnctypeX]:
    crypt_length = response[0] ^ 0xEC
    offset = 1 + crypt_length
    server_length = response[offset] ^ 0xEA
    offset += 1
    server_challenge = response[offset : offset + server_length]
    offset += server_length
    cipher = EnctypeX("nNfhSl", challenge, server_challenge)
    return cipher.decrypt(response[offset:]), cipher


def decrypt_server_browser(response: bytes, challenge: bytes) -> bytes:
    body, _ = decrypt_server_browser_stream(response, challenge)
    return body


class AvailabilityQRTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = test_config()
        self.state = ServerState(120, 60)
        self.protocol = AvailabilityQRProtocol(self.config, self.state)

    def test_availability_and_qr_registration(self) -> None:
        source = ("10.0.0.10", 30123)
        request = b"\x09\x00\x00\x00\x00FruitNinjaand\x00"
        response = self.protocol.handle_datagram(request, source)
        self.assertEqual(response, b"\xfe\xfd\x09" + b"\x00" * 8)

        instance = b"ABCD"
        heartbeat = (
            b"\x03"
            + instance
            + b"gamename\x00FruitNinjaandam\x00"
            + b"hostname\x00LAN Host\x00"
            + b"hostport\x006500\x00"
            + b"maxplayers\x002\x00"
            + b"gamemode\x00openstaging\x00"
            + b"natneg\x001\x00\x00"
        )
        challenge_packet = self.protocol.handle_datagram(heartbeat, source)
        self.assertIsNotNone(challenge_packet)
        assert challenge_packet is not None
        self.assertEqual(challenge_packet[:7], b"\xfe\xfd\x01" + instance)
        challenge = challenge_packet[7:-1].decode("ascii")
        proof = gsseckey(challenge, "nNfhSl")
        registered = self.protocol.handle_datagram(
            b"\x01" + instance + proof.encode("ascii") + b"\x00",
            source,
        )
        self.assertEqual(registered, b"\xfe\xfd\x0a" + instance)
        self.assertEqual(len(self.state.active_servers("FruitNinjaand")), 1)
        self.assertEqual(
            self.state.active_servers("FruitNinjaand")[0].keys["gamename"],
            "FruitNinjaandam",
        )

    def test_oversized_qr_packet_is_rejected(self) -> None:
        packet = b"\x09" + b"x" * self.config.limits.qr_packet_bytes
        with self.assertRaisesRegex(ValueError, "exceeds configured limit"):
            self.protocol.handle_datagram(packet, ("10.0.0.10", 30123))


class ServerBrowserTests(unittest.TestCase):
    def test_registered_host_is_encrypted_into_server_list(self) -> None:
        config = test_config()
        state = ServerState(120, 60)
        source = ("10.0.0.10", 30123)
        state.report_server(
            source,
            b"ABCD",
            {
                "gamename": "FruitNinjaand",
                "hostname": "LAN Host",
                "hostport": "6500",
                "maxplayers": "2",
                "gamemode": "openstaging",
                "natneg": "1",
            },
        )
        state.register_server(source)
        challenge = b"12345678"
        request = server_browser_request(
            challenge,
            "\\hostname\\maxplayers\\gamemode",
        )
        response = ServerBrowserServer(config, state).handle_request(request, "10.0.0.20")
        self.assertIsNotNone(response)
        assert response is not None
        body = decrypt_server_browser(response, challenge)
        self.assertTrue(body.startswith(socket.inet_aton("10.0.0.20") + struct.pack(">H", 6500)))
        self.assertIn(socket.inet_aton("10.0.0.10"), body)
        self.assertIn(b"LAN Host\x00", body)
        self.assertIn(b"2\x00", body)
        self.assertIn(b"openstaging\x00", body)
        self.assertTrue(body.endswith(b"\x00\xff\xff\xff\xff"))

    def test_server_list_advertises_observed_source_endpoint(self) -> None:
        config = test_config()
        state = ServerState(120, 60)
        source = ("198.51.100.10", 30123)
        state.report_server(
            source,
            b"INET",
            {
                "gamename": "FruitNinjaand",
                "hostname": "Internet Host",
                "hostport": "6500",
                "localip0": "10.0.0.10",
                "localport": "6500",
                "natneg": "1",
            },
        )
        state.register_server(source)
        challenge = b"INTERNET"
        response = ServerBrowserServer(config, state).handle_request(
            server_browser_request(challenge, ""),
            "198.51.100.20",
        )
        self.assertIsNotNone(response)
        assert response is not None
        body = decrypt_server_browser(response, challenge)
        entry = body[8:]
        self.assertTrue(entry[0] & 16)
        self.assertEqual(entry[1:5], socket.inet_aton(source[0]))
        self.assertEqual(struct.unpack_from(">H", entry, 5)[0], source[1])

    def test_automatch_game_alias_is_accepted(self) -> None:
        config = test_config()
        state = ServerState(120, 60)
        challenge = b"AUTOMATC"
        request = server_browser_request(
            challenge,
            "",
            options=4,
            query_game="FruitNinjaandam",
        )
        response = ServerBrowserServer(config, state).handle_request(request, "10.0.0.20")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertTrue(
            decrypt_server_browser(response, challenge).endswith(b"\x00\xff\xff\xff\xff")
        )


    def test_invalid_advertised_address_fields_do_not_break_discovery(self) -> None:
        config = test_config()
        state = ServerState(120, 60)
        source = ("10.0.0.10", 40000)
        state.report_server(
            source,
            b"BAD1",
            {
                "gamename": "FruitNinjaand",
                "hostport": "99999",
                "localip0": "not-an-ip-address",
                "localport": "99999",
            },
        )
        state.register_server(source)
        response = ServerBrowserServer(config, state).handle_request(
            server_browser_request(b"BADFIELD", ""),
            "10.0.0.20",
        )

        self.assertIsNotNone(response)


class NatNegTests(unittest.TestCase):
    def test_two_clients_are_paired_by_cookie(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"CKIE"
        first_addr = ("10.0.0.10", 40000)
        second_addr = ("10.0.0.20", 40001)
        first = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 1)) + b"\x00" * 6
        second = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 1)) + b"\x00" * 6
        first_responses = protocol.handle_datagram(first, first_addr)
        self.assertEqual(len(first_responses), 1)
        self.assertEqual(first_responses[0][0][7], NN_INIT_ACK)

        responses = protocol.handle_datagram(second, second_addr)
        self.assertEqual(responses[0][0][7], NN_INIT_ACK)
        connect_packets = [(data, addr) for data, addr in responses if data[7] == NN_CONNECT]
        self.assertEqual(len(connect_packets), 2)
        by_destination = {addr: data for data, addr in connect_packets}
        self.assertEqual(by_destination[first_addr][12:16], socket.inet_aton(second_addr[0]))
        self.assertEqual(struct.unpack(">H", by_destination[first_addr][16:18])[0], second_addr[1])
        self.assertEqual(by_destination[second_addr][12:16], socket.inet_aton(first_addr[0]))


    def test_pairing_emits_structured_lifecycle_events(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"LOG1"
        first = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 0, 1)) + b"\x00" * 6
        second = MAGIC + bytes((3, NN_INIT)) + cookie + bytes((0, 1, 1)) + b"\x00" * 6

        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            protocol.handle_datagram(first, ("10.0.0.10", 40000))
            protocol.handle_datagram(second, ("10.0.0.20", 40001))

        output = "\n".join(captured.output)
        self.assertIn("event=session_created session=4c4f4731", output)
        self.assertIn("event=peer_updated session=4c4f4731", output)
        self.assertIn("event=peers_paired session=4c4f4731", output)

    def test_report_logs_negotiation_outcome(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"RSLT"
        packet = (
            MAGIC
            + bytes((3, NN_REPORT))
            + cookie
            + bytes((0, 1, 3))
            + struct.pack("<II", 5, 3)
            + b"FruitNinjaand\x00".ljust(50, b"\x00")
        )

        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            responses = protocol.handle_datagram(packet, ("10.0.0.10", 40000))

        acknowledgement = packet[:7] + bytes((NN_REPORT_ACK,)) + packet[8:]
        self.assertEqual(responses, [(acknowledgement, ("10.0.0.10", 40000))])
        output = "\n".join(captured.output)
        self.assertIn("event=client_report session=52534c54", output)
        self.assertIn("peer=1 result=ping_timeout result_code=3", output)
        self.assertIn("nat_type=symmetric nat_type_code=5", output)
        self.assertIn("mapping=incremental mapping_code=3", output)

    def test_short_report_is_rejected(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        packet = MAGIC + bytes((3, NN_REPORT)) + b"SHRT"

        with self.assertRaisesRegex(ValueError, "short NatNeg report"):
            protocol.handle_datagram(packet, ("10.0.0.10", 40000))

    def test_oversized_natneg_packet_is_rejected(self) -> None:
        config = test_config()
        protocol = NatNegProtocol(config, ServerState(120, 60))
        packet = MAGIC + b"\x00" * (config.limits.natneg_packet_bytes - len(MAGIC) + 1)
        with self.assertRaisesRegex(ValueError, "exceeds configured limit"):
            protocol.handle_datagram(packet, ("10.0.0.10", 40000))

    def test_invalid_natneg_client_index_is_rejected(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        packet = MAGIC + bytes((3, NN_INIT)) + b"BAD1" + bytes((0, 2, 1)) + b"\x00" * 6

        with self.assertRaisesRegex(ValueError, "client index"):
            protocol.handle_datagram(packet, ("10.0.0.10", 40000))


if __name__ == "__main__":
    unittest.main()
