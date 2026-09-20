#!/usr/bin/env python3
from __future__ import annotations

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port


def printable_ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else "." for b in data)


def query(client: IsoTpClient, pid: int) -> bytes:
    response = client.request(bytes([0x09, pid]))
    print(f"Mode 09 PID 0x{pid:02X}: {response.hex(' ').upper()}")
    return response


def payload_after_header(response: bytes, pid: int) -> bytes:
    # Typical Mode 09 physical response: 49 <pid> <message/count byte> <data...>
    if len(response) >= 3 and response[0] == 0x49 and response[1] == pid:
        return response[3:]
    return b""


def main() -> None:
    port = find_port()
    print(f"CANable: {port}")
    print("Reading standard OBD-II vehicle/PCM identification data.\n")

    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=0x7E0, rx_id=0x7E8, timeout=3.0)

        vin_response = query(client, 0x02)
        cal_response = query(client, 0x04)
        ecu_response = query(client, 0x0A)

    vin = payload_after_header(vin_response, 0x02).rstrip(b"\x00 ")
    cal = payload_after_header(cal_response, 0x04).rstrip(b"\x00 ")
    ecu = payload_after_header(ecu_response, 0x0A).rstrip(b"\x00 ")

    if vin:
        print(f"\nVIN:            {printable_ascii(vin)}")
    if cal:
        print(f"Calibration ID: {printable_ascii(cal)}")
    if ecu:
        print(f"ECU name:       {printable_ascii(ecu)}")

    print("\nSave this output in KOER research. Calibration ID is especially useful for matching the PCM/protocol.")


if __name__ == "__main__":
    main()
