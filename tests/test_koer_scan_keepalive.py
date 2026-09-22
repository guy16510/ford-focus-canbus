import unittest
from unittest.mock import patch

from scripts import koer_scan_keepalive as kk


class FakeLog:
    def __init__(self):
        self.lines = []

    def write(self, message=""):
        self.lines.append(message)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.timeout = 2.0

    def send_payload(self, payload):
        self.sent.append(payload)

    def receive_payload(self):
        if not self.replies:
            raise TimeoutError("no reply")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ReceiveResponseTests(unittest.TestCase):
    def test_normal_response_restores_timeout(self):
        client = FakeClient([bytes.fromhex("7F 31 21")])
        log = FakeLog()
        response = kk.receive_response(client, 0x31, log, initial_timeout=0.01)
        self.assertEqual(bytes.fromhex("7F 31 21"), response)
        self.assertEqual(2.0, client.timeout)

    def test_response_pending_waits_for_final(self):
        client = FakeClient(
            [bytes.fromhex("7F 31 78"), bytes.fromhex("71 03 02 82 20 00 00 00")]
        )
        log = FakeLog()
        response = kk.receive_response(
            client,
            0x31,
            log,
            initial_timeout=0.01,
            p2_star_timeout=0.01,
            pending_overall_timeout=0.1,
        )
        self.assertEqual(bytes.fromhex("71 03 02 82 20 00 00 00"), response)
        self.assertTrue(any("responsePending" in line for line in log.lines))


class TesterPresentTests(unittest.TestCase):
    def test_positive_tester_present(self):
        client = FakeClient([bytes.fromhex("7E 00")])
        log = FakeLog()
        self.assertTrue(kk.tester_present(client, log))
        self.assertEqual([bytes.fromhex("3E 00")], client.sent)

    def test_rejected_tester_present_disables_keepalive(self):
        client = FakeClient([bytes.fromhex("7F 3E 12")])
        log = FakeLog()
        self.assertFalse(kk.tester_present(client, log))
        self.assertTrue(any("rejected" in line for line in log.lines))


class PollingTests(unittest.TestCase):
    def test_busy_then_completed_with_keepalive(self):
        client = FakeClient(
            [
                bytes.fromhex("7E 00"),  # initial TesterPresent capability check
                bytes.fromhex("7E 00"),  # keepalive before first result poll
                bytes.fromhex("7F 31 21"),
                bytes.fromhex("7E 00"),  # keepalive before second result poll
                bytes.fromhex("71 03 02 82 20 00 00 00"),
            ]
        )
        log = FakeLog()
        with patch.object(kk, "TESTER_PRESENT_INTERVAL", 0.0), patch.object(
            kk.time, "sleep", return_value=None
        ):
            self.assertTrue(
                kk.poll_koer_results(client, log, max_seconds=1.0, poll_interval=0.0)
            )
        self.assertIn(bytes.fromhex("3E 00"), client.sent)
        self.assertIn(bytes.fromhex("31 03 02 82"), client.sent)

    def test_silent_result_timeout_gets_keepalive_then_completes(self):
        client = FakeClient(
            [
                bytes.fromhex("7E 00"),  # initial capability check
                bytes.fromhex("7E 00"),  # keepalive before first result
                TimeoutError("silent result"),
                bytes.fromhex("7E 00"),  # timeout recovery keepalive
                bytes.fromhex("7E 00"),  # keepalive before next result
                bytes.fromhex("71 03 02 82 20 00 00 00"),
            ]
        )
        log = FakeLog()
        with patch.object(kk, "TESTER_PRESENT_INTERVAL", 0.0), patch.object(
            kk.time, "sleep", return_value=None
        ):
            self.assertTrue(
                kk.poll_koer_results(client, log, max_seconds=1.0, poll_interval=0.0)
            )
        self.assertTrue(any("silent timeout" in line for line in log.lines))


if __name__ == "__main__":
    unittest.main()
