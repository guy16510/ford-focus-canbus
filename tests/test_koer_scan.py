import unittest
from unittest.mock import patch

from scripts import koer_scan as ks


class FakeLog:
    def __init__(self):
        self.lines = []

    def write(self, message=""):
        self.lines.append(message)


class FakeReceiveClient:
    def __init__(self, replies):
        self.replies = list(replies)

    def receive_payload(self):
        if not self.replies:
            raise TimeoutError("empty")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Type2RoutineInfoTests(unittest.TestCase):
    def test_active(self):
        info = ks.decode_type2_routine_info(bytes.fromhex("22"))
        self.assertEqual(2, info.routine_type)
        self.assertEqual("active", info.status)
        self.assertIsNone(info.on_demand_dtc)

    def test_aborted(self):
        self.assertEqual(
            "aborted", ks.decode_type2_routine_info(bytes.fromhex("21")).status
        )

    def test_completed_result_requires_dtc_and_decodes_it(self):
        info = ks.decode_type2_routine_info(
            bytes.fromhex("20 00 00 00"), expect_dtc=True
        )
        self.assertEqual("completed", info.status)
        self.assertEqual(bytes.fromhex("00 00 00"), info.on_demand_dtc)

    def test_completed_result_missing_dtc_rejected(self):
        with self.assertRaises(ValueError):
            ks.decode_type2_routine_info(bytes.fromhex("20"), expect_dtc=True)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            ks.decode_type2_routine_info(b"")

    def test_wrong_type_rejected(self):
        with self.assertRaises(ValueError):
            ks.decode_type2_routine_info(bytes.fromhex("10"))

    def test_unknown_status_rejected(self):
        with self.assertRaises(ValueError):
            ks.decode_type2_routine_info(bytes.fromhex("2F"))

    def test_extra_data_preserved(self):
        info = ks.decode_type2_routine_info(
            bytes.fromhex("20 01 02 03 AA BB"), expect_dtc=True
        )
        self.assertEqual(bytes.fromhex("01 02 03"), info.on_demand_dtc)
        self.assertEqual(bytes.fromhex("AA BB"), info.extra)


class ProtocolConstantTests(unittest.TestCase):
    def test_exact_koer_payloads(self):
        self.assertEqual(bytes.fromhex("31 01 02 82"), ks.KOER_START)
        self.assertEqual(bytes.fromhex("31 03 02 82"), ks.KOER_RESULTS)

    def test_extended_is_primary(self):
        self.assertEqual("extended", ks.SESSION_CANDIDATES[0][0])
        self.assertEqual(bytes.fromhex("10 03"), ks.SESSION_CANDIDATES[0][1])

    def test_environmental_nrc_mapping_matches_ford_metadata(self):
        expected = {
            0x89: "vehicleSpeedTooLow",
            0x8A: "throttlePedalTooHigh",
            0x8B: "throttlePedalTooLow",
            0x8C: "transmissionRangeNotInNeutral",
            0x8D: "transmissionRangeNotInGear",
            0x8F: "brakeSwitchesNotClosed",
            0x90: "shifterLeverNotInPark",
            0x91: "torqueConverterClutchLocked",
        }
        for code, name in expected.items():
            with self.subTest(code=code):
                self.assertEqual(name, ks.NRC[code])


class StartHandlingTests(unittest.TestCase):
    @staticmethod
    def candidate():
        return ks.CandidateState(
            "extended", bytes.fromhex("10 03"), bytes.fromhex("50 03")
        )

    def test_start_active_is_accepted_only_with_type2_info(self):
        log = FakeLog()
        with patch.object(
            ks,
            "enter_session",
            return_value=(True, bytes.fromhex("50 03 00 32 01 F4")),
        ), patch.object(
            ks, "exchange_busy_retry", return_value=bytes.fromhex("71 01 02 82 22")
        ):
            outcome, info = ks.try_start_candidate(object(), self.candidate(), log)
        self.assertEqual("accepted", outcome)
        self.assertEqual("active", info.status)

    def test_positive_start_without_routineinfo_is_terminal(self):
        log = FakeLog()
        with patch.object(
            ks,
            "enter_session",
            return_value=(True, bytes.fromhex("50 03 00 32 01 F4")),
        ), patch.object(
            ks, "exchange_busy_retry", return_value=bytes.fromhex("71 01 02 82")
        ):
            outcome, info = ks.try_start_candidate(object(), self.candidate(), log)
        self.assertEqual("terminal", outcome)
        self.assertIsNone(info)

    def test_session_nrc_requests_bounded_session_fallback(self):
        log = FakeLog()
        candidate = self.candidate()
        with patch.object(
            ks,
            "enter_session",
            return_value=(True, bytes.fromhex("50 03 00 32 01 F4")),
        ), patch.object(
            ks, "exchange_busy_retry", return_value=bytes.fromhex("7F 31 7E")
        ):
            outcome, _ = ks.try_start_candidate(object(), candidate, log)
        self.assertEqual("wrong-session", outcome)
        self.assertTrue(candidate.session_mismatch)

    def test_condition_nrc_does_not_switch_session(self):
        log = FakeLog()
        candidate = self.candidate()
        with patch.object(
            ks,
            "enter_session",
            return_value=(True, bytes.fromhex("50 03 00 32 01 F4")),
        ), patch.object(
            ks, "exchange_busy_retry", return_value=bytes.fromhex("7F 31 22")
        ):
            outcome, _ = ks.try_start_candidate(object(), candidate, log)
        self.assertEqual("condition", outcome)
        self.assertFalse(candidate.session_mismatch)


class ResultHandlingTests(unittest.TestCase):
    def test_completed_with_dtc_is_success(self):
        log = FakeLog()
        with patch.object(
            ks,
            "exchange_busy_retry",
            return_value=bytes.fromhex("71 03 02 82 20 00 00 00"),
        ):
            self.assertTrue(
                ks.poll_koer_results(object(), log, max_seconds=0.5, poll_interval=0)
            )

    def test_completed_without_dtc_is_never_assumed_success(self):
        log = FakeLog()
        with patch.object(
            ks,
            "exchange_busy_retry",
            return_value=bytes.fromhex("71 03 02 82 20"),
        ):
            self.assertFalse(
                ks.poll_koer_results(object(), log, max_seconds=0.5, poll_interval=0)
            )

    def test_aborted_is_failure(self):
        log = FakeLog()
        with patch.object(
            ks,
            "exchange_busy_retry",
            return_value=bytes.fromhex("71 03 02 82 21"),
        ):
            self.assertFalse(
                ks.poll_koer_results(object(), log, max_seconds=0.5, poll_interval=0)
            )

    def test_active_then_completed(self):
        log = FakeLog()
        replies = iter(
            [
                bytes.fromhex("71 03 02 82 22"),
                bytes.fromhex("71 03 02 82 20 00 00 00"),
            ]
        )
        with patch.object(
            ks, "exchange_busy_retry", side_effect=lambda *args, **kwargs: next(replies)
        ):
            self.assertTrue(
                ks.poll_koer_results(object(), log, max_seconds=1, poll_interval=0)
            )


class PendingHandlingTests(unittest.TestCase):
    def test_response_pending_waits_for_final_without_resending(self):
        log = FakeLog()
        client = FakeReceiveClient(
            [bytes.fromhex("7F 31 78"), bytes.fromhex("71 01 02 82 22")]
        )
        result = ks.receive_final(
            client, 0x31, log, overall_timeout=1.0, p2_star_seconds=0.1
        )
        self.assertEqual(bytes.fromhex("71 01 02 82 22"), result)
        self.assertTrue(any("responsePending" in line for line in log.lines))


if __name__ == "__main__":
    unittest.main()
