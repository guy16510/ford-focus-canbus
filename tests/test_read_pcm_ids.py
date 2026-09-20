import unittest

from scripts.read_pcm_ids import request_for, printable_value


class ReadPcmIdsTests(unittest.TestCase):
    def test_request_is_read_data_by_identifier(self):
        self.assertEqual(request_for(0xF188), bytes.fromhex("22 F1 88"))

    def test_printable_value_preserves_ascii_and_marks_binary(self):
        self.assertEqual(printable_value(b"BG9T-14C094-AM"), "BG9T-14C094-AM")
        self.assertEqual(printable_value(bytes.fromhex("01 02")), "(no printable characters)")


if __name__ == "__main__":
    unittest.main()
