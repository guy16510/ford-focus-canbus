import unittest

from scripts import read_codes as rc


class DtcDecodeTests(unittest.TestCase):
    def test_decodes_powertrain_code(self):
        self.assertEqual("P0301", rc.decode_dtc_pair(bytes.fromhex("03 01")))

    def test_decodes_chassis_body_network_prefixes(self):
        self.assertEqual("C1234", rc.decode_dtc_pair(bytes.fromhex("52 34")))
        self.assertEqual("B2100", rc.decode_dtc_pair(bytes.fromhex("A1 00")))
        self.assertEqual("U0100", rc.decode_dtc_pair(bytes.fromhex("C1 00")))

    def test_zero_pair_is_ignored(self):
        self.assertIsNone(rc.decode_dtc_pair(bytes.fromhex("00 00")))

    def test_parse_mode03_response(self):
        dtcs, trailing = rc.parse_dtc_response(
            bytes.fromhex("43 03 01 04 20 00 00"), 0x43
        )
        self.assertEqual(["P0301", "P0420"], dtcs)
        self.assertEqual(b"", trailing)

    def test_parse_reports_malformed_trailing_byte(self):
        dtcs, trailing = rc.parse_dtc_response(bytes.fromhex("43 03 01 AA"), 0x43)
        self.assertEqual(["P0301"], dtcs)
        self.assertEqual(bytes.fromhex("AA"), trailing)

    def test_parse_mil_status(self):
        status = rc.parse_mil_status(bytes.fromhex("41 01 82 07 65 04"))
        self.assertTrue(status.on)
        self.assertEqual(2, status.stored_dtc_count)

    def test_read_only_allowlist_rejects_clear_codes(self):
        class Client:
            def request(self, payload):
                return b""

        with self.assertRaises(ValueError):
            rc.request_read_only(Client(), b"\x04")


if __name__ == "__main__":
    unittest.main()
