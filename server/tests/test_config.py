import json
import tempfile
import unittest
from pathlib import Path

from fruitspy.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server_root = Path(__file__).resolve().parents[1]

    def test_unified_config_loads(self) -> None:
        config = load_config(self.server_root / "config.json")
        self.assertEqual(config.bind_host, "0.0.0.0")
        self.assertEqual(config.limits.natneg_packet_bytes, 512)
        self.assertEqual(config.limits.peerchat_channels, 1024)
        self.assertEqual(config.limits.peerchat_channels_per_client, 16)
        self.assertEqual(config.limits.peerchat_keys_per_collection, 64)
        self.assertEqual(config.limits.peerchat_commands_per_second, 30)
        self.assertEqual(config.limits.peerchat_command_burst, 60)
        self.assertEqual(config.limits.peerchat_state_creations_per_second, 8)
        self.assertEqual(config.limits.peerchat_state_creation_burst, 32)
        self.assertEqual(config.timeouts.state_expiry_interval_seconds, 5)
        self.assertEqual(config.limits.udp_global_packets_per_second, 4096)
        self.assertEqual(config.limits.udp_global_burst, 8192)
        self.assertEqual(config.limits.udp_source_violation_burst, 30)
        self.assertEqual(config.timeouts.udp_source_ban_seconds, 60)
        self.assertEqual(config.relay.policy, "auto")
        self.assertEqual(config.relay.fallback_seconds, 3)
        self.assertEqual(config.metrics.bind_host, "127.0.0.1")
        self.assertEqual(config.metrics.port, 9108)

    def test_removed_profile_fields_are_rejected(self) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["mode"] = "internet"
        payload["advertise_host"] = "games.example.net"
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "unknown top-level configuration fields: advertise_host, mode",
            ):
                load_config(invalid)

    def test_packet_limits_are_validated(self) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["natneg_packet_bytes"] = 12
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "natneg_packet_bytes"):
                load_config(invalid)

    def test_metrics_listener_is_loopback_only_and_uses_a_distinct_port(
        self,
    ) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["metrics"]["bind_host"] = "0.0.0.0"
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "loopback"):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["metrics"]["port"] = payload["ports"]["peerchat_tcp"]
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differ"):
                load_config(invalid)

    def test_admission_limits_and_deadlines_are_validated(self) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["connections_per_source"] = 300
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "connections_per_source"):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["timeouts"]["peerchat_handshake_seconds"] = 0
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "peerchat_handshake_seconds"):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["timeouts"]["state_expiry_interval_seconds"] = 0
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "state_expiry_interval_seconds",
            ):
                load_config(invalid)
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["udp_global_packets_per_second"] = (
            payload["limits"]["udp_packets_per_second"] - 1
        )
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "udp_global_packets_per_second",
            ):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["timeouts"]["udp_source_ban_seconds"] = 0
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "udp_source_ban_seconds"):
                load_config(invalid)

    def test_peerchat_collection_limits_are_validated(self) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["peerchat_channels"] = 0
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "peerchat_channels"):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["peerchat_channels_per_client"] = 0
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "peerchat_channels_per_client",
            ):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["peerchat_keys_per_collection"] = 65536
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "peerchat_keys_per_collection",
            ):
                load_config(invalid)

    def test_peerchat_client_budgets_are_validated(self) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["peerchat_command_burst"] = (
            payload["limits"]["peerchat_commands_per_second"] - 1
        )
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "peerchat_command_burst"):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["peerchat_state_creation_burst"] = (
            payload["limits"]["peerchat_state_creations_per_second"] - 1
        )
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(
                ValueError,
                "peerchat_state_creation_burst",
            ):
                load_config(invalid)

    def test_relay_policy_and_limits_are_validated(self) -> None:
        source = self.server_root / "config.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["relay"]["policy"] = "always"
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "relay policy"):
                load_config(invalid)

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["relay"]["byte_burst"] = payload["relay"]["bytes_per_second"] - 1
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "byte_burst"):
                load_config(invalid)


if __name__ == "__main__":
    unittest.main()
