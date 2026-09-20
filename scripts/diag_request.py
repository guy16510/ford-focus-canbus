#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from datetime import datetime

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port


BLOCKED_SERVICES = {
    0x11: "ECUReset",
    0x27: "SecurityAccess",
    0x2E: "WriteDataByIdentifier",
    0x34: "RequestDownload",
    0x35: "RequestUpload",
    0x36: "TransferData",
    0x37: "RequestTransferExit",
    0x3D: "WriteMemoryByAddress",
}


def parse_hex(value: str) -> bytes:
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", value)
    if len(cleaned) % 2:
        raise argparse.ArgumentTypeError("payload must contain complete hex bytes")
    if not cleaned:
        raise argparse.ArgumentTypeError("payload cannot be empty")
    return bytes.fromhex(cleaned)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guarded physical ISO-TP diagnostic request to the Focus PCM"
    )
    parser.add_argument("--payload", required=True, type=parse_hex, help='e.g. "22 F1 90"')
    parser.add_argument("--tx-id", type=lambda x: int(x, 0), default=0x7E0)
    parser.add_argument("--rx-id", type=lambda x: int(x, 0), default=0x7E8)
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--execute", action="store_true", help="actually transmit the request")
    parser.add_argument(
        "--allow-routine",
        action="store_true",
        help="allow UDS RoutineControl (0x31); use only for a verified routine",
    )
    args = parser.parse_args()

    service = args.payload[0]
    if service in BLOCKED_SERVICES:
        raise SystemExit(
            f"Refusing service 0x{service:02X} ({BLOCKED_SERVICES[service]}). "
            "This lab intentionally excludes module programming/security operations."
        )
    if service == 0x31 and not args.allow_routine:
        raise SystemExit(
            "RoutineControl (0x31) is potentially state-changing. "
            "Verify the exact Ford KOER routine first, then add --allow-routine."
        )

    print(f"TX ID:   0x{args.tx_id:X}")
    print(f"RX ID:   0x{args.rx_id:X}")
    print(f"Payload: {args.payload.hex(' ').upper()}")

    if not args.execute:
        print("\nDRY RUN, nothing transmitted. Add --execute only after verifying the request.")
        return

    port = find_port()
    print(f"Port:    {port}")
    print(f"Time:    {datetime.now().isoformat(timespec='seconds')}")

    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=args.tx_id, rx_id=args.rx_id, timeout=args.timeout)
        response = client.request(args.payload)

    print(f"Response: {response.hex(' ').upper()}")
    if len(response) >= 3 and response[0] == 0x7F:
        print(
            f"Negative response: original service=0x{response[1]:02X}, "
            f"NRC=0x{response[2]:02X}"
        )


if __name__ == "__main__":
    main()
