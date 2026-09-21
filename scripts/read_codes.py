#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port

PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8

# SAE J1979 read-only services. Mode 04 (clear/reset emissions DTCs) is
# intentionally not implemented anywhere in this tool.
DTC_MODES = (
    (0x03, 0x43, "Stored / confirmed"),
    (0x07, 0x47, "Pending"),
    (0x0A, 0x4A, "Permanent"),
)


@dataclass(frozen=True)
class MilStatus:
    on: bool
    stored_dtc_count: int


def fmt(data: bytes) -> str:
    return data.hex(" ").upper()


def decode_dtc_pair(pair: bytes) -> str | None:
    """Decode one SAE J2012/J1979 two-byte DTC value (for example P0301)."""
    if len(pair) != 2:
        raise ValueError("DTC pair must be exactly two bytes")
    first, second = pair
    if first == 0 and second == 0:
        return None

    system = "PCBU"[(first >> 6) & 0x03]
    second_digit = (first >> 4) & 0x03
    third_digit = first & 0x0F
    fourth_digit = (second >> 4) & 0x0F
    fifth_digit = second & 0x0F
    return f"{system}{second_digit}{third_digit:X}{fourth_digit:X}{fifth_digit:X}"


def parse_dtc_response(response: bytes, positive_service: int) -> tuple[list[str], bytes]:
    """Return decoded DTCs and any malformed trailing bytes.

    The application payload begins with the positive service byte (43/47/4A)
    followed by zero or more two-byte DTC values. 00 00 entries are padding/no-DTC.
    """
    if not response or response[0] != positive_service:
        raise ValueError(
            f"expected positive service 0x{positive_service:02X}, got {fmt(response) or '<empty>'}"
        )

    payload = response[1:]
    complete_len = len(payload) - (len(payload) % 2)
    dtcs: list[str] = []
    for offset in range(0, complete_len, 2):
        decoded = decode_dtc_pair(payload[offset : offset + 2])
        if decoded is not None:
            dtcs.append(decoded)
    return dtcs, payload[complete_len:]


def parse_mil_status(response: bytes) -> MilStatus:
    """Decode Mode 01 PID 01 MIL state and emissions-DTC count."""
    if len(response) < 3 or response[:2] != bytes.fromhex("41 01"):
        raise ValueError(f"unexpected Mode 01 PID 01 response: {fmt(response)}")
    a = response[2]
    return MilStatus(on=bool(a & 0x80), stored_dtc_count=a & 0x7F)


def describe_negative(response: bytes) -> str | None:
    if len(response) >= 3 and response[0] == 0x7F:
        return f"negative response to 0x{response[1]:02X}, NRC 0x{response[2]:02X}"
    return None


def request_read_only(client: IsoTpClient, payload: bytes) -> bytes:
    """Send a deliberately allowlisted read-only OBD request."""
    if payload not in (bytes.fromhex("01 01"), b"\x03", b"\x07", b"\x0A"):
        raise ValueError(f"request is not on read-only allowlist: {fmt(payload)}")
    return client.request(payload)


def read_codes(client: IsoTpClient) -> dict[str, list[str]]:
    print("\n=== CHECK ENGINE / OBD-II CODES ===")
    print("Read-only scan only. Nothing will be cleared.\n")

    try:
        response = request_read_only(client, bytes.fromhex("01 01"))
        print(f"TX 0x{PCM_TX_ID:X}: 01 01  [MIL status / DTC count]")
        print(f"RX 0x{PCM_RX_ID:X}: {fmt(response)}")
        status = parse_mil_status(response)
        print(f"MIL / Check Engine Light: {'ON' if status.on else 'OFF'}")
        print(f"PCM-reported stored emissions DTC count: {status.stored_dtc_count}")
    except (TimeoutError, ValueError) as error:
        print(f"MIL status: unavailable ({error})")

    results: dict[str, list[str]] = {}
    for request_service, positive_service, label in DTC_MODES:
        payload = bytes((request_service,))
        print(f"\nTX 0x{PCM_TX_ID:X}: {fmt(payload)}  [{label} DTCs]")
        try:
            response = request_read_only(client, payload)
        except TimeoutError:
            print(f"{label}: timeout / unsupported")
            results[label] = []
            continue

        print(f"RX 0x{PCM_RX_ID:X}: {fmt(response)}")
        negative = describe_negative(response)
        if negative is not None:
            print(f"{label}: unavailable ({negative})")
            results[label] = []
            continue

        try:
            dtcs, trailing = parse_dtc_response(response, positive_service)
        except ValueError as error:
            print(f"{label}: could not decode ({error})")
            results[label] = []
            continue

        results[label] = dtcs
        if dtcs:
            print(f"{label}: {', '.join(dtcs)}")
        else:
            print(f"{label}: none reported")
        if trailing:
            print(f"WARNING: undecoded trailing response byte(s): {fmt(trailing)}")

    print("\nNo DTCs were erased. Mode 04 / Clear DTCs is intentionally not implemented.")
    return results


def run(args: argparse.Namespace) -> int:
    print("FORD FOCUS CHECK-ENGINE CODE READER")
    print("===================================")
    print("Ignition must be ON. The engine may be running or stopped.")
    print("This reads PCM emissions codes only; it does NOT clear anything.")
    if not args.execute:
        print("\nDRY RUN - no CAN frames transmitted.")
        print("Would send only: 01 01, 03, 07, 0A to PCM 0x7E0 -> 0x7E8.")
        return 0

    confirm = input("\nType READ to scan the PCM: ").strip().upper()
    if confirm != "READ":
        print("Cancelled. Nothing transmitted.")
        return 0

    port = args.port or find_port()
    print(f"CANable: {port}")
    try:
        with Slcan(port) as bus:
            bus.open_channel(500000, silent=False)
            client = IsoTpClient(bus, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=3.0)
            read_codes(client)
    except KeyboardInterrupt:
        print("\nStopped. CAN channel closed.")
        return 130
    except Exception as error:
        print(f"\nStopped: {error}")
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read Ford Focus PCM OBD-II emissions DTCs")
    parser.add_argument("--execute", action="store_true", help="transmit read-only OBD-II requests")
    parser.add_argument("--port", help="CANable serial port; defaults to CANABLE_PORT/autodetection")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
