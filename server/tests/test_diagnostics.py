import tempfile
import unittest
from pathlib import Path

from fruitspy.diagnostics import (
    diagnosis_lines,
    parse_counter_samples,
    read_snapshot,
    write_snapshot,
)


def sample(metric: str, label: str, value: str, count: int) -> tuple[str, float]:
    return f'{metric}{{{label}="{value}"}}', float(count)


class DiagnosticMetricsTests(unittest.TestCase):
    def test_parser_keeps_only_privacy_safe_diagnostic_counters(self) -> None:
        payload = """\
# HELP fruitspy_qr_events_total events
fruitspy_qr_events_total{event="registered"} 2
fruitspy_peerchat_clients 4
fruitspy_natneg_setup_seconds_count{path="relay"} 1
fruitspy_relay_packets_total{result="forwarded"} 12
"""

        samples = parse_counter_samples(payload)

        self.assertEqual(
            samples,
            {
                'fruitspy_qr_events_total{event="registered"}': 2.0,
                'fruitspy_relay_packets_total{result="forwarded"}': 12.0,
            },
        )

    def test_snapshot_round_trip_contains_only_supplied_aggregates(self) -> None:
        samples = dict(
            [sample("fruitspy_qr_events_total", "event", "registered", 1)]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "before.json"

            captured_at = write_snapshot(path, samples)

            self.assertIn("+00:00", captured_at)
            self.assertEqual(read_snapshot(path), samples)
            output = path.read_text(encoding="utf-8")
            self.assertNotIn("source", output)
            self.assertNotIn("session", output)

    def test_failed_game_after_clean_relay_is_classified_beyond_server(self) -> None:
        after = dict(
            [
                sample("fruitspy_qr_events_total", "event", "availability_accepted", 2),
                sample("fruitspy_qr_events_total", "event", "challenge_issued", 1),
                sample("fruitspy_qr_events_total", "event", "registered", 1),
                sample("fruitspy_peerchat_events_total", "event", "registered", 2),
                sample("fruitspy_peerchat_events_total", "event", "joined", 2),
                sample("fruitspy_discovery_results_total", "result", "nonempty", 1),
                sample("fruitspy_natneg_session_events_total", "event", "created", 1),
                sample("fruitspy_natneg_session_events_total", "event", "paired", 1),
                sample("fruitspy_natneg_outcomes_total", "outcome", "relay", 1),
                sample("fruitspy_relay_events_total", "event", "activated", 1),
                sample("fruitspy_relay_events_total", "event", "ready", 1),
                sample("fruitspy_relay_events_total", "event", "established", 1),
                sample("fruitspy_relay_packets_total", "result", "forwarded", 42),
                sample("fruitspy_relay_packets_total", "result", "dropped", 0),
            ]
        )

        lines = diagnosis_lines({}, after, "failed")

        self.assertIn(
            "PASS stage=path outcome=relay established=1 forwarded=42 dropped=0",
            lines,
        )
        self.assertEqual(
            lines[-1],
            "SUMMARY result=failed failure_domain=client_or_gameplay "
            "evidence=relay_forwarding_without_drops",
        )

    def test_incomplete_relay_handshake_is_server_pipeline_failure(self) -> None:
        after = dict(
            [
                sample("fruitspy_qr_events_total", "event", "availability_accepted", 2),
                sample("fruitspy_qr_events_total", "event", "challenge_issued", 1),
                sample("fruitspy_qr_events_total", "event", "registered", 1),
                sample("fruitspy_peerchat_events_total", "event", "registered", 2),
                sample("fruitspy_peerchat_events_total", "event", "joined", 2),
                sample("fruitspy_discovery_results_total", "result", "nonempty", 1),
                sample("fruitspy_natneg_session_events_total", "event", "created", 1),
                sample("fruitspy_natneg_session_events_total", "event", "paired", 1),
                sample("fruitspy_relay_events_total", "event", "activated", 1),
                (
                    'fruitspy_protocol_rejections_total'
                    '{service="natneg",reason="capacity"}',
                    1.0,
                ),
            ]
        )

        lines = diagnosis_lines({}, after, "failed")

        self.assertIn(
            "FAIL stage=path outcome=relay_handshake_incomplete activated=1 ready=0",
            lines,
        )
        self.assertIn(
            "INFO rejection=fruitspy_protocol_rejections_total"
            '{service="natneg",reason="capacity"} delta=1',
            lines,
        )
        self.assertEqual(
            lines[-1],
            "SUMMARY result=failed failure_domain=server_pipeline",
        )

    def test_counter_reset_invalidates_comparison(self) -> None:
        series, _ = sample(
            "fruitspy_natneg_session_events_total",
            "event",
            "paired",
            0,
        )

        with self.assertRaisesRegex(ValueError, "process restarted"):
            diagnosis_lines({series: 4.0}, {series: 0.0})


if __name__ == "__main__":
    unittest.main()
