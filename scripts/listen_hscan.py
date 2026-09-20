#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime

from focus_can.slcan import Slcan, find_port


def main() -> None:
    port = find_port()
    print(f"CANable: {port}")
    print("Opening HS-CAN at 500 kbit/s in silent/listen-only mode.")
    print("Press Ctrl-C to stop.\n")

    count = 0
    with Slcan(port=port) as can:
        can.open_channel(500000, silent=True)
        try:
            while True:
                frame = can.read_frame(timeout=1.0)
                if frame is None:
                    continue
                count += 1
                ts = datetime.now().isoformat(timespec="milliseconds")
                print(
                    f"{ts}  {frame.arbitration_id:03X}  "
                    f"[{len(frame.data)}]  {frame.data.hex(' ').upper()}"
                )
        except KeyboardInterrupt:
            pass

    print(f"\nStopped after {count} CAN frames.")


if __name__ == "__main__":
    main()
