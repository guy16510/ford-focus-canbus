import tempfile
import unittest
from pathlib import Path

from scripts.koer import (
    ObdReading,
    PreflightSnapshot,
    decode_obd_response,
    diff_snapshots,
    evaluate_preflight,
    manual_wizard,
    parse_supported_pids,
    render_dry_run_manual_steps,
    render_failure,
    save_failed_preflight,
)


class KoerWizardTests(unittest.TestCase):
    def test_manual_wizard_requires_cluster_confirmation_for_success(self):
        answers = iter(["", "", "", "", ""])
        output = []

        confirmed = manual_wizard(input_fn=lambda _: next(answers), output_fn=output.append)

        self.assertTrue(confirmed)
        self.assertIn("STEP 4 - CLEAR MYKEY", "\n".join(output))
        self.assertIn("Procedure complete.", output[-1])

    def test_manual_wizard_does_not_claim_success_without_cluster_confirmation(self):
        answers = iter(["", "", "", "no", ""])
        output = []

        confirmed = manual_wizard(input_fn=lambda _: next(answers), output_fn=output.append)

        self.assertFalse(confirmed)
        self.assertIn("did not succeed", "\n".join(output))

    def test_failure_message_preserves_final_response(self):
        message = render_failure("KOER results", b"\x7f\x31\x22")

        self.assertIn("KOER DID NOT COMPLETE", message)
        self.assertIn("RX 0x7E8: 7F 31 22", message)
        self.assertIn("conditionsNotCorrect", message)

    def test_dry_run_manual_steps_are_only_a_simulation(self):
        output = []

        render_dry_run_manual_steps(output_fn=output.append)

        rendered = "\n".join(output)
        self.assertIn("SIMULATION", rendered)
        self.assertNotIn("Press ENTER", rendered)
        self.assertNotIn("Wrap the", rendered)


class KoerPreflightTests(unittest.TestCase):
    def test_supported_bitmap_maps_bits_to_pid_numbers(self):
        supported = parse_supported_pids(0x00, bytes.fromhex("BE 1F A8 13"))

        self.assertIn(0x01, supported)
        self.assertIn(0x05, supported)
        self.assertIn(0x0C, supported)
        self.assertIn(0x20, supported)
        self.assertNotIn(0x02, supported)

    def test_decodes_required_standard_obd_values(self):
        cases = (
            (0x0C, bytes.fromhex("41 0C 0C B0"), 812.0),
            (0x0D, bytes.fromhex("41 0D 00"), 0.0),
            (0x05, bytes.fromhex("41 05 70"), 72.0),
            (0x1F, bytes.fromhex("41 1F 00 A4"), 164.0),
            (0x42, bytes.fromhex("41 42 37 78"), 14.2),
            (0x11, bytes.fromhex("41 11 1F"), 100 * 31 / 255),
            (0x04, bytes.fromhex("41 04 40"), 100 * 64 / 255),
        )
        for pid, response, expected in cases:
            with self.subTest(pid=pid):
                reading = decode_obd_response(pid, response)
                self.assertAlmostEqual(expected, reading.value, places=2)

    def test_preflight_stops_when_engine_is_not_running(self):
        snapshot = PreflightSnapshot(readings={
            0x0C: ObdReading(0x0C, "RPM", 0.0, "rpm"),
            0x0D: ObdReading(0x0D, "Speed", 0.0, "km/h"),
        })

        decision = evaluate_preflight(snapshot)

        self.assertFalse(decision.can_attempt)
        self.assertIn("engine is not running", decision.blockers)

    def test_preflight_stops_when_vehicle_is_moving(self):
        snapshot = PreflightSnapshot(readings={
            0x0C: ObdReading(0x0C, "RPM", 812.0, "rpm"),
            0x0D: ObdReading(0x0D, "Speed", 1.0, "km/h"),
        })

        decision = evaluate_preflight(snapshot)

        self.assertFalse(decision.can_attempt)
        self.assertIn("vehicle speed is not zero", decision.blockers)

    def test_unknown_values_do_not_become_guessed_blockers(self):
        snapshot = PreflightSnapshot(readings={
            0x0C: ObdReading(0x0C, "RPM", 812.0, "rpm"),
            0x0D: ObdReading(0x0D, "Speed", 0.0, "km/h"),
            0x05: ObdReading(0x05, "Coolant", 20.0, "C"),
            0x42: ObdReading(0x42, "Voltage", 14.2, "V"),
        })

        decision = evaluate_preflight(snapshot)

        self.assertTrue(decision.can_attempt)
        self.assertIn("exact Ford coolant threshold is UNKNOWN", decision.notes)

    def test_snapshot_diff_identifies_changed_values(self):
        previous = PreflightSnapshot(readings={
            0x0C: ObdReading(0x0C, "RPM", 800.0, "rpm"),
            0x05: ObdReading(0x05, "Coolant", 70.0, "C"),
        })
        current = PreflightSnapshot(readings={
            0x0C: ObdReading(0x0C, "RPM", 812.0, "rpm"),
            0x05: ObdReading(0x05, "Coolant", 70.0, "C"),
        })

        changes = diff_snapshots(previous, current)

        self.assertEqual(["RPM: 800 rpm -> 812 rpm"], changes)

    def test_failed_preflight_log_round_trips_without_vehicle_identifiers(self):
        snapshot = PreflightSnapshot(readings={
            0x0C: ObdReading(0x0C, "RPM", 812.0, "rpm"),
        })
        with tempfile.TemporaryDirectory() as directory:
            path = save_failed_preflight(snapshot, Path(directory), bytes.fromhex("7F 31 22"))

            text = path.read_text()

        self.assertIn('"response": "7F 31 22"', text)
        self.assertIn('"RPM"', text)


if __name__ == "__main__":
    unittest.main()
