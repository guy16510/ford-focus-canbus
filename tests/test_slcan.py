import unittest

from focus_can.slcan import CanFrame, encode_frame, parse_frame


class SlcanCodecTests(unittest.TestCase):
    def test_parse_standard_data_frame(self):
        frame = parse_frame(b"t7E884100BE1FA813\r")
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.arbitration_id, 0x7E8)
        self.assertEqual(frame.data, bytes.fromhex("41 00 BE 1F A8 13"))
        self.assertFalse(frame.extended)

    def test_encode_standard_data_frame(self):
        encoded = encode_frame(CanFrame(0x7E0, bytes.fromhex("02 01 00 00 00 00 00 00")))
        self.assertEqual(encoded, b"t7E080201000000000000\r")

    def test_non_frame_response_is_ignored(self):
        self.assertIsNone(parse_frame(b"V1234\r"))
        self.assertIsNone(parse_frame(b"\r"))


if __name__ == "__main__":
    unittest.main()
