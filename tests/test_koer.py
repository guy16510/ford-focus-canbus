import unittest

from scripts.koer import manual_wizard, render_failure


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


if __name__ == "__main__":
    unittest.main()
