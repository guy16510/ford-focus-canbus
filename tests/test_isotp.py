import unittest

from focus_can.isotp import IsoTpClient
from focus_can.slcan import CanFrame


class FakeBus:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    def send(self, arbitration_id, data, extended=False):
        self.sent.append(CanFrame(arbitration_id, bytes(data), extended=extended))

    def read_frame(self, timeout=1.0):
        if self.incoming:
            return self.incoming.pop(0)
        return None


class IsoTpTests(unittest.TestCase):
    def test_single_frame_request_and_response(self):
        bus = FakeBus(
            [CanFrame(0x7E8, bytes.fromhex("06 41 00 BE 1F A8 13 00"))]
        )
        client = IsoTpClient(bus, tx_id=0x7E0, rx_id=0x7E8)
        response = client.request(bytes.fromhex("01 00"))

        self.assertEqual(response, bytes.fromhex("41 00 BE 1F A8 13"))
        self.assertEqual(bus.sent[0].arbitration_id, 0x7E0)
        self.assertEqual(bus.sent[0].data, bytes.fromhex("02 01 00 00 00 00 00 00"))

    def test_multiframe_response_sends_flow_control(self):
        payload = b"ABCDEFGHIJ"
        first = bytes([0x10, len(payload)]) + payload[:6]
        second = bytes([0x21]) + payload[6:] + b"\x00" * 3
        bus = FakeBus([
            CanFrame(0x7E8, first),
            CanFrame(0x7E8, second),
        ])
        client = IsoTpClient(bus, tx_id=0x7E0, rx_id=0x7E8)
        response = client.receive_payload()

        self.assertEqual(response, payload)
        self.assertEqual(bus.sent[0].data[:3], bytes.fromhex("30 00 00"))


if __name__ == "__main__":
    unittest.main()
