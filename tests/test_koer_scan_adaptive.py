import unittest
from unittest.mock import patch

from scripts import koer_scan_adaptive as adaptive


class FakeLog:
    def __init__(self):
        self.lines = []

    def write(self, message=""):
        self.lines.append(message)


class AdaptivePollTests(unittest.TestCase):
    def test_busy_then_silent_then_completed(self):
        log = FakeLog()
        replies = [
            bytes.fromhex("7F 31 21"),
            TimeoutError("silent"),
            bytes.fromhex("71 03 02 82 20 00 00 00"),
        ]
        with patch.object(adaptive.keepalive, "tester_present", return_value=True), patch.object(
            adaptive.keepalive, "exchange_short", side_effect=replies
        ), patch.object(
            adaptive,
            "wait_with_keepalive",
            side_effect=lambda client, log, seconds, enabled: enabled,
        ):
            self.assertTrue(
                adaptive.poll_koer_results(object(), log, max_seconds=30.0, poll_interval=0.0)
            )

        self.assertTrue(any("SILENT PHASE" in line for line in log.lines))
        self.assertTrue(any("COMPLETED" in line for line in log.lines))

    def test_completed_is_only_positive_success(self):
        log = FakeLog()
        response = bytes.fromhex("71 03 02 82 20 00 00 00")
        self.assertEqual("completed", adaptive.decode_result(response, log))

    def test_busy_negative_is_not_completion(self):
        log = FakeLog()
        self.assertEqual("other", adaptive.decode_result(bytes.fromhex("7F 31 21"), log))

    def test_silence_does_not_mean_success(self):
        log = FakeLog()
        with patch.object(adaptive.keepalive, "tester_present", return_value=True), patch.object(
            adaptive.keepalive, "exchange_short", side_effect=TimeoutError("silent")
        ), patch.object(
            adaptive,
            "wait_with_keepalive",
            side_effect=lambda client, log, seconds, enabled: enabled,
        ), patch.object(
            adaptive.time,
            "monotonic",
            side_effect=[0.0, 0.0, 1.0, 2.0, 31.0, 31.0],
        ):
            self.assertFalse(
                adaptive.poll_koer_results(object(), log, max_seconds=30.0, poll_interval=0.0)
            )

        self.assertFalse(any("reports COMPLETED" in line for line in log.lines))


if __name__ == "__main__":
    unittest.main()
