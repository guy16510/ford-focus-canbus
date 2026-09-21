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

    def test_parse_can_mode03_uses_count_byte(self):
        dtcs, trailing = rc.parse_dtc_response(
            bytes.fromhex("43 02 03 01 04 20"), 0x43
        )
        self.assertEqual(["P0301", "P0420"], dtcs)
        self.assertEqual(b"", trailing)

    def test_live_focus_shape_decodes_p0131_not_p0101(self):
        dtcs, trailing = rc.parse_dtc_response(bytes.fromhex("43 01 01 31"), 0x43)
        self.assertEqual(["P0131"], dtcs)
        self.assertEqual(b"", trailing)

    def test_zero_count_response(self):
        dtcs, trailing = rc.parse_dtc_response(bytes.fromhex("47 00"), 0x47)
        self.assertEqual([], dtcs)
        self.assertEqual(b"", trailing)

    def test_declared_count_must_have_enough_data(self):
        with self.assertRaises(ValueError):
            rc.parse_dtc_response(bytes.fromhex("43 02 03 01"), 0x43)

    def test_extra_nonzero_bytes_are_reported(self):
        dtcs, trailing = rc.parse_dtc_response(bytes.fromhex("43 01 03 01 AA"), 0x43)
        self.assertEqual(["P0301"], dtcs)
        self.assertEqual(bytes.fromhex("AA"), trailing)

    def test_zero_padding_after_declared_records_is_ignored(self):
        dtcs, trailing = rc.parse_dtc_response(bytes.fromhex("43 01 03 01 00 00"), 0x43)
        self.assertEqual(["P0301"], dtcs)
        self.assertEqual(b"", trailing)

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
