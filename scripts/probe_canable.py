#!/usr/bin/env python3
from __future__ import annotations

from focus_can.slcan import Slcan, find_port


def printable(raw: bytes) -> str:
    if not raw:
        return "<no bytes returned>"
    return repr(raw)


def main() -> None:
    port = find_port()
    print(f"Opening CANable at {port}")

    with Slcan(port=port) as can:
        close_response = can.close_channel()
        print(f"Close-channel response: {printable(close_response)}")

        version = can.version()
        print(f"Version response: {printable(version)}")

    print("Probe complete. No CAN traffic was transmitted to the vehicle bus.")


if __name__ == "__main__":
    main()
