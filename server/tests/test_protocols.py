import socket
import struct
import unittest
from unittest import mock

from fruitspy.availability_qr import (
    QR_AMPLIFICATION_DENOMINATOR,
    QR_AMPLIFICATION_NUMERATOR,
    AvailabilityQRProtocol,
)
from fruitspy.crypto import EnctypeX, gsseckey
from fruitspy.natneg import (
    MAGIC,
    NATNEG_AMPLIFICATION_LIMIT,
    NN_ADDRESS_CHECK,
    NN_CONNECT,
    NN_INIT,
    NN_INIT_ACK,
    NN_NATIFY_REQUEST,
    NN_PREINIT,
    NN_REPORT,
    NN_REPORT_ACK,
    NatNegProtocol,
)
from fruitspy.server_browser import ServerBrowserServer
from fruitspy.state import ServerState
from tests.helpers import test_config

def natneg_init(cookie: bytes, client_index: int, version: int = 3) -> bytes:
    return (
        MAGIC
        + bytes((version, NN_INIT))
        + cookie
        + bytes((0, client_index, 0))
        + b"\x00" * 6
    )




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

    def test_all_qr_responses_stay_within_two_to_one_limit(self) -> None:
        source = ("10.0.0.10", 30123)
        requests = [
            b"\x09\x00\x00\x00\x00\x00",
            b"\x09\x00\x00\x00\x00FruitNinjaand\x00",
            b"\x08ABCD",
            b"\x03ABCDgamename\x00FruitNinjaand\x00",
        ]
        responses = [
            self.protocol.handle_datagram(request, source)
            for request in requests
        ]
        challenge = responses[-1]
        assert challenge is not None
        proof = gsseckey(
            challenge[7:-1].decode("ascii"),
            self.config.game.secret_key,
        )
        challenge_request = b"\x01ABCD" + proof.encode("ascii") + b"\x00"
        requests.append(challenge_request)
        responses.append(self.protocol.handle_datagram(challenge_request, source))

        measurements = [
            (len(request), len(response))
            for request, response in zip(requests, responses, strict=True)
            if response is not None
        ]
        self.assertEqual(
            measurements,
            [(6, 11), (19, 11), (5, 7), (28, 28), (34, 7)],
        )
        for request_bytes, response_bytes in measurements:
            self.assertLessEqual(
                response_bytes * QR_AMPLIFICATION_DENOMINATOR,
                request_bytes * QR_AMPLIFICATION_NUMERATOR,
            )

    def test_qr_response_above_amplification_limit_is_suppressed(self) -> None:
        class OversizedHeartbeatProtocol(AvailabilityQRProtocol):
            def _heartbeat(
                self,
                data: bytes,
                addr: tuple[str, int],
            ) -> bytes:
                return b"x" * 15

        protocol = OversizedHeartbeatProtocol(self.config, self.state)
        with self.assertLogs("fruitspy.availability_qr", level="WARNING") as captured:
            response = protocol.handle_datagram(
                b"\x03ABCD\x00\x00",
                ("10.0.0.10", 30123),
            )

        self.assertIsNone(response)
        self.assertIn(
            "event=response_suppressed",
            "\n".join(captured.output),
        )


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
        server = ServerBrowserServer(config, state)
        response = server.handle_request(request, "10.0.0.20")
        self.assertIsNotNone(response)
        assert response is not None
        body = decrypt_server_browser(response, challenge)
        self.assertTrue(body.startswith(socket.inet_aton("10.0.0.20") + struct.pack(">H", 6500)))
        self.assertIn(socket.inet_aton("10.0.0.10"), body)
        self.assertIn(b"LAN Host\x00", body)
        self.assertIn(b"2\x00", body)
        self.assertIn(b"openstaging\x00", body)
        self.assertTrue(body.endswith(b"\x00\xff\xff\xff\xff"))
        metrics = server.metrics.render().decode("utf-8")
        self.assertIn(
            'fruitspy_discovery_requests_total{kind="list"} 1',
            metrics,
        )
        self.assertIn(
            'fruitspy_discovery_results_total{result="nonempty"} 1',
            metrics,
        )
        self.assertNotIn("10.0.0.20", metrics)

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


    def test_all_natneg_responses_stay_within_three_to_one_limit(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"AMPN"
        first_addr = ("10.0.0.10", 40000)
        second_addr = ("10.0.0.20", 40001)
        cases = [
            (natneg_init(cookie, 0), first_addr),
            (natneg_init(cookie, 1), second_addr),
            (
                MAGIC + bytes((3, NN_ADDRESS_CHECK)) + b"ADDR",
                ("10.0.0.30", 40002),
            ),
            (
                MAGIC + bytes((3, NN_NATIFY_REQUEST)) + b"NTFY",
                ("10.0.0.30", 40002),
            ),
            (
                MAGIC + bytes((3, NN_PREINIT)) + b"PREI",
                ("10.0.0.30", 40002),
            ),
            (
                MAGIC
                + bytes((3, NN_REPORT))
                + cookie
                + bytes((0, 1, 0))
                + b"\x00" * 8,
                second_addr,
            ),
        ]

        measurements = []
        for request, source in cases:
            responses = protocol.handle_datagram(request, source)
            measurements.append(
                (
                    len(request),
                    sum(len(response) for response, _ in responses),
                )
            )

        self.assertEqual(
            measurements,
            [(21, 21), (21, 61), (12, 21), (12, 12), (12, 12), (23, 23)],
        )
        for request_bytes, response_bytes in measurements:
            self.assertLessEqual(
                response_bytes,
                request_bytes * NATNEG_AMPLIFICATION_LIMIT,
            )

    def test_natneg_suppresses_immediate_and_delayed_over_amplification(
        self,
    ) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"CAPS"
        protocol.handle_datagram(
            natneg_init(cookie, 0),
            ("10.0.0.10", 40000),
        )

        with (
            mock.patch.object(
                NatNegProtocol,
                "_connect_packet",
                return_value=b"x" * 22,
            ),
            self.assertLogs("fruitspy.natneg", level="WARNING") as captured,
        ):
            responses = protocol.handle_datagram(
                natneg_init(cookie, 1),
                ("10.0.0.20", 40001),
            )

        self.assertEqual(responses, [])
        self.assertEqual(protocol._fallbacks, {})
        self.assertIn("event=response_suppressed", "\n".join(captured.output))

        transport = mock.Mock()
        protocol.transport = transport
        with (
            mock.patch.object(
                NatNegProtocol,
                "_relay_ping_packet",
                return_value=b"x" * 23,
            ),
            self.assertLogs("fruitspy.natneg", level="WARNING") as captured,
        ):
            protocol._activate_relay(cookie)

        self.assertEqual(protocol._relays, {})
        transport.sendto.assert_not_called()
        self.assertIn("phase=relay_fallback", "\n".join(captured.output))

    def test_direct_outcome_metrics_are_aggregated_without_client_data(
        self,
    ) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"PRIV"
        first_addr = ("10.0.0.10", 40000)
        second_addr = ("10.0.0.20", 40001)
        protocol.handle_datagram(natneg_init(cookie, 0), first_addr)
        protocol.handle_datagram(natneg_init(cookie, 1), second_addr)
        for index, source in enumerate((first_addr, second_addr)):
            report = (
                MAGIC
                + bytes((3, NN_REPORT))
                + cookie
                + bytes((0, index, 1))
                + b"\x00" * 8
            )
            protocol.handle_datagram(report, source)

        metrics = protocol.metrics.render().decode("utf-8")
        self.assertIn(
            'fruitspy_natneg_reports_total{result="success"} 2',
            metrics,
        )
        self.assertIn(
            'fruitspy_natneg_outcomes_total{outcome="direct"} 1',
            metrics,
        )
        self.assertIn(
            'fruitspy_natneg_setup_seconds_count{path="direct"} 1',
            metrics,
        )
        self.assertNotIn(cookie.hex(), metrics)
        self.assertNotIn(first_addr[0], metrics)

    def test_pairing_emits_structured_lifecycle_events(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"LOG1"
        first = (
            MAGIC
            + bytes((3, NN_INIT))
            + cookie
            + bytes((0, 0, 1))
            + socket.inet_aton("192.168.1.10")
            + struct.pack(">H", 6500)
        )
        second = (
            MAGIC
            + bytes((3, NN_INIT))
            + cookie
            + bytes((0, 1, 0))
            + socket.inet_aton("192.168.2.20")
            + struct.pack(">H", 32000)
        )

        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            protocol.handle_datagram(first, ("10.0.0.10", 40000))
            protocol.handle_datagram(second, ("10.0.0.20", 40001))

        output = "\n".join(captured.output)
        self.assertIn("event=session_created session=4c4f4731", output)
        self.assertIn("event=peer_added session=4c4f4731", output)
        self.assertIn("event=peers_paired session=4c4f4731", output)
        self.assertIn(
            "peer=0 source=('10.0.0.10', 40000) port_type=0 "
            "use_game_port=True local_endpoint=('192.168.1.10', 6500)",
            output,
        )
        self.assertIn(
            "peer=1 source=('10.0.0.20', 40001) port_type=0 "
            "use_game_port=False local_endpoint=('192.168.2.20', 32000)",
            output,
        )

    def test_duplicate_init_from_claimed_endpoint_is_idempotent(self) -> None:
        state = ServerState(120, 60)
        protocol = NatNegProtocol(test_config(), state)
        cookie = b"DUPL"
        address = ("10.0.0.10", 40000)

        protocol.handle_datagram(natneg_init(cookie, 0, version=2), address)
        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            responses = protocol.handle_datagram(
                natneg_init(cookie, 0, version=3),
                address,
            )

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0][7], NN_INIT_ACK)
        self.assertEqual(set(state.nat_sessions[cookie].peers), {0})
        self.assertEqual(state.nat_sessions[cookie].peers[0].version, 3)
        self.assertIn("event=peer_refreshed", "\n".join(captured.output))

    def test_active_cookie_collision_cannot_replace_claimed_index(self) -> None:
        state = ServerState(120, 60)
        protocol = NatNegProtocol(test_config(), state)
        cookie = b"COLL"
        claimed = ("10.0.0.10", 40000)
        conflicting = ("10.0.0.20", 40001)
        protocol.handle_datagram(natneg_init(cookie, 0), claimed)
        last_seen = state.nat_sessions[cookie].last_seen

        with self.assertLogs("fruitspy.natneg", level="WARNING") as captured:
            responses = protocol.handle_datagram(
                natneg_init(cookie, 0),
                conflicting,
            )

        self.assertEqual(responses, [])
        self.assertEqual(state.nat_sessions[cookie].peers[0].address, claimed)
        self.assertEqual(state.nat_sessions[cookie].last_seen, last_seen)
        self.assertIn("event=peer_rejected", "\n".join(captured.output))
        self.assertIn("reason=index_claimed", "\n".join(captured.output))

    def test_one_endpoint_cannot_claim_both_peer_indexes(self) -> None:
        state = ServerState(120, 60)
        protocol = NatNegProtocol(test_config(), state)
        cookie = b"SPUF"
        address = ("10.0.0.10", 40000)
        protocol.handle_datagram(natneg_init(cookie, 0), address)

        with self.assertLogs("fruitspy.natneg", level="WARNING") as captured:
            responses = protocol.handle_datagram(natneg_init(cookie, 1), address)

        self.assertEqual(responses, [])
        self.assertEqual(set(state.nat_sessions[cookie].peers), {0})
        self.assertIn("reason=endpoint_claimed", "\n".join(captured.output))

    def test_third_peer_is_rejected_after_pairing(self) -> None:
        state = ServerState(120, 60)
        protocol = NatNegProtocol(test_config(), state)
        cookie = b"THRD"
        first = ("10.0.0.10", 40000)
        second = ("10.0.0.20", 40001)
        third = ("10.0.0.30", 40002)
        protocol.handle_datagram(natneg_init(cookie, 0), first)
        protocol.handle_datagram(natneg_init(cookie, 1), second)

        with self.assertLogs("fruitspy.natneg", level="WARNING") as captured:
            responses = protocol.handle_datagram(natneg_init(cookie, 0), third)

        self.assertEqual(responses, [])
        self.assertEqual(
            {peer.address for peer in state.nat_sessions[cookie].peers.values()},
            {first, second},
        )
        self.assertIn("reason=session_full", "\n".join(captured.output))

    def test_expired_cookie_can_be_reclaimed(self) -> None:
        state = ServerState(120, 1)
        protocol = NatNegProtocol(test_config(), state)
        cookie = b"REUS"
        expired = ("10.0.0.10", 40000)
        replacement = ("10.0.0.20", 40001)
        protocol.handle_datagram(natneg_init(cookie, 0), expired)
        old_session = state.nat_sessions[cookie]
        old_session.last_seen -= 2

        responses = protocol.handle_datagram(natneg_init(cookie, 0), replacement)

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0][0][7], NN_INIT_ACK)
        self.assertIsNot(state.nat_sessions[cookie], old_session)
        self.assertEqual(state.nat_sessions[cookie].peers[0].address, replacement)

    def test_report_logs_boolean_negotiation_outcome(self) -> None:
        protocol = NatNegProtocol(test_config(), ServerState(120, 60))
        cookie = b"RSLT"
        packet = (
            MAGIC
            + bytes((3, NN_REPORT))
            + cookie
            + bytes((0, 1, 0))
            + struct.pack("<II", 5, 3)
            + b"FruitNinjaand\x00".ljust(50, b"\x00")
        )

        with self.assertLogs("fruitspy.natneg", level="INFO") as captured:
            responses = protocol.handle_datagram(packet, ("10.0.0.10", 40000))

        acknowledgement = packet[:7] + bytes((NN_REPORT_ACK,)) + packet[8:]
        self.assertEqual(responses, [(acknowledgement, ("10.0.0.10", 40000))])
        output = "\n".join(captured.output)
        self.assertIn("event=client_report session=52534c54", output)
        self.assertIn("peer=1 result=failure result_code=0", output)
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
