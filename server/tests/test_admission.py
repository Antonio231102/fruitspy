import unittest

from fruitspy.admission import ConnectionAdmission, SourceRateLimiter
from fruitspy.state import ServerState


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SourceRateLimiterTests(unittest.TestCase):
    def test_burst_is_bounded_and_refills(self) -> None:
        clock = FakeClock()
        limiter = SourceRateLimiter(2, 2, 8, 60, clock=clock)

        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertFalse(limiter.allow("192.0.2.1"))

        clock.now = 0.5
        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertFalse(limiter.allow("192.0.2.1"))

    def test_source_table_stays_bounded_and_expires_idle_entries(self) -> None:
        clock = FakeClock()
        limiter = SourceRateLimiter(1, 1, 1, 10, clock=clock)

        self.assertTrue(limiter.allow("192.0.2.1"))
        self.assertFalse(limiter.allow("192.0.2.2"))

        clock.now = 11
        self.assertTrue(limiter.allow("192.0.2.2"))


class ConnectionAdmissionTests(unittest.TestCase):
    def test_total_and_per_source_limits_are_released(self) -> None:
        admission = ConnectionAdmission(total_limit=2, per_source_limit=1)

        self.assertTrue(admission.acquire("192.0.2.1"))
        self.assertFalse(admission.acquire("192.0.2.1"))
        self.assertTrue(admission.acquire("192.0.2.2"))
        self.assertFalse(admission.acquire("192.0.2.3"))

        admission.release("192.0.2.1")
        self.assertTrue(admission.acquire("192.0.2.3"))
        self.assertEqual(admission.total, 2)

        admission.release("192.0.2.2")
        admission.release("192.0.2.3")
        self.assertEqual(admission.total, 0)

class StateCapacityTests(unittest.TestCase):
    def test_reported_server_capacity_is_bounded(self) -> None:
        state = ServerState(120, 60, max_reported_servers=1)
        state.report_server(
            ("192.0.2.1", 6500),
            b"ONE1",
            {"gamename": "FruitNinjaand"},
        )

        with self.assertRaisesRegex(ValueError, "reported server capacity"):
            state.report_server(
                ("192.0.2.2", 6500),
                b"TWO2",
                {"gamename": "FruitNinjaand"},
            )

    def test_nat_session_capacity_is_bounded(self) -> None:
        state = ServerState(120, 60, max_nat_sessions=1)
        state.touch_nat_peer(b"ONE1", 0, ("192.0.2.1", 6500), 3)

        with self.assertRaisesRegex(ValueError, "NatNeg session capacity"):
            state.touch_nat_peer(b"TWO2", 0, ("192.0.2.2", 6500), 3)



if __name__ == "__main__":
    unittest.main()
