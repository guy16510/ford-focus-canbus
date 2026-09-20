#!/usr/bin/env python3
from __future__ import annotations

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port


def main() -> None:
    port = find_port()
    print(f"CANable: {port}")
    print("Opening HS-CAN actively at 500 kbit/s.")
    print("Sending harmless OBD-II Mode 01 PID 00 to PCM 0x7E0.\n")

    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=0x7E0, rx_id=0x7E8, timeout=2.5)
        response = client.request(bytes.fromhex("01 00"))

    print(f"PCM response: {response.hex(' ').upper()}")
    if len(response) >= 6 and response[0:2] == bytes.fromhex("41 00"):
        bitmap = int.from_bytes(response[2:6], "big")
        print(f"Success. PCM responded normally. PID support bitmap: 0x{bitmap:08X}")
    elif len(response) >= 3 and response[0] == 0x7F:
        print(
            f"PCM returned a negative diagnostic response: "
            f"service=0x{response[1]:02X}, NRC=0x{response[2]:02X}"
        )
        raise SystemExit(2)
    else:
        print("Unexpected response format. Save the raw response before proceeding.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
