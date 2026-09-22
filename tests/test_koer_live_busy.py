import unittest
from unittest.mock import patch

from scripts import koer_scan as ks


class FakeLog:
    def __init__(self):
        self.lines = []

    def write(self, message=""):
        self.lines.append(message)


class LiveBusyPollingTests(unittest.TestCase):
    def test_busy_repeat_is_polled_once_per_interval_then_completion(self):
        log = FakeLog()
        replies = iter(
            [
                bytes.fromhex("7F 31 21"),
                bytes.fromhex("7F 31 21"),
                bytes.fromhex("71 03 02 82 20 00 00 00"),
            ]
        )
        calls = []

        def fake_exchange(*args, **kwargs):
            calls.append(kwargs)
            return next(replies)

        with patch.object(ks, "exchange_busy_retry", side_effect=fake_exchange), patch.object(
            ks.time, "sleep", return_value=None
        ):
            self.assertTrue(
                ks.poll_koer_results(object(), log, max_seconds=5.0, poll_interval=0.01)
            )

        self.assertEqual(3, len(calls))
        self.assertTrue(all(call.get("max_busy_retries") == 0 for call in calls))
        rendered = "\n".join(log.lines)
        self.assertIn("busyRepeatRequest #1", rendered)
        self.assertIn("busyRepeatRequest #2", rendered)
        self.assertIn("COMPLETED", rendered)

    def test_operator_guidance_contains_required_manual_inputs(self):
        log = FakeLog()
        ks.print_operator_actions(log, coolant_c=59.0, runtime_s=39.0)
        rendered = "\n".join(log.lines)
        self.assertIn("BRAKE pedal", rendered)
        self.assertIn("steering wheel", rendered)
        self.assertIn("59 C", rendered)
        self.assertIn("39 sec", rendered)
        self.assertIn("No key foil yet", rendered)


if __name__ == "__main__":
    unittest.main()
