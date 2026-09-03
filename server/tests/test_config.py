import json
import tempfile
import unittest
from pathlib import Path

from fruitspy.config import load_config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server_root = Path(__file__).resolve().parents[1]

    def test_lan_profile_loads_with_automatic_address(self) -> None:
        config = load_config(self.server_root / "config.json")
        self.assertEqual(config.mode, "lan")
        self.assertTrue(config.advertise_host)
        self.assertEqual(config.limits.natneg_packet_bytes, 512)

    def test_internet_profile_requires_explicit_public_host(self) -> None:
        source = self.server_root / "config.internet.example.json"
        config = load_config(source)
        self.assertEqual(config.mode, "internet")
        self.assertEqual(config.advertise_host, "games.example.net")

        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["advertise_host"] = "auto"
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "explicit advertise_host"):
                load_config(invalid)

    def test_packet_limits_are_validated(self) -> None:
        source = self.server_root / "config.internet.example.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["limits"]["natneg_packet_bytes"] = 12
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "natneg_packet_bytes"):
                load_config(invalid)

    def test_admission_limits_and_deadlines_are_validated(self) -> None:
        source = self.server_root / "config.internet.example.json"
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


if __name__ == "__main__":
    unittest.main()
