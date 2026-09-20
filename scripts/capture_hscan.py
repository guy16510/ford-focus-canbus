#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from focus_can.slcan import Slcan, find_port


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Focus HS-CAN in silent mode")
    parser.add_argument("--seconds", type=float, default=30.0, help="capture duration")
    parser.add_argument("--output", help="output log path")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    output = Path(args.output) if args.output else logs / f"hscan-{datetime.now():%Y%m%d-%H%M%S}.log"

    port = find_port()
    print(f"CANable: {port}")
    print(f"Capturing silently for {args.seconds:g}s -> {output}")

    started = datetime.now()
    count = 0
    with output.open("w", encoding="utf-8") as fh, Slcan(port=port) as can:
        can.open_channel(500000, silent=True)
        while (datetime.now() - started).total_seconds() < args.seconds:
            frame = can.read_frame(timeout=0.5)
            if frame is None:
                continue
            count += 1
            ts = datetime.now().isoformat(timespec="milliseconds")
            line = (
                f"{ts} {frame.arbitration_id:03X} {len(frame.data)} "
                f"{frame.data.hex().upper()}\n"
            )
            fh.write(line)

    print(f"Captured {count} frames.")


if __name__ == "__main__":
    main()
