#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port

PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8

EXTENDED_SESSION = bytes.fromhex("10 03")
KOER_START = bytes.fromhex("31 01 02 02")
KOER_RESULTS = bytes.fromhex("31 03 02 02")
TESTER_PRESENT = bytes.fromhex("3E 00")

NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x78: "responsePending",
    0x7E: "subFunctionNotSupportedInActiveSession",
    0x7F: "serviceNotSupportedInActiveSession",
    0x81: "rpmTooHigh",
    0x82: "rpmTooLow",
    0x83: "engineIsRunning",
    0x84: "engineIsNotRunning",
    0x85: "engineRunTimeTooLow",
    0x86: "temperatureTooHigh",
    0x87: "temperatureTooLow",
    0x88: "vehicleSpeedTooHigh",
    0x8B: "transmissionRangeNotInNeutral",
    0x8F: "shifterLeverNotInPark",
    0x92: "voltageTooHigh",
    0x93: "voltageTooLow",
}


def fmt(data: bytes) -> str:
    return data.hex(" ").upper()


def describe_negative(response: bytes) -> str:
    if len(response) >= 3 and response[0] == 0x7F:
        return f"service=0x{response[1]:02X} NRC=0x{response[2]:02X} ({NRC.get(response[2], 'unknown')})"
    return "not a negative response"


def request_wait_pending(
    client: IsoTpClient,
    payload: bytes,
    *,
    label: str,
    overall_timeout: float = 45.0,
) -> bytes:
    print(f"TX 0x{PCM_TX_ID:X}: {fmt(payload)}  [{label}]")
    client.send_payload(payload)

    deadline = time.monotonic() + overall_timeout
    while time.monotonic() < deadline:
        try:
            response = client.receive_payload()
        except TimeoutError:
            continue

        print(f"RX 0x{PCM_RX_ID:X}: {fmt(response)}")
        if (
            len(response) >= 3
            and response[0] == 0x7F
            and response[1] == payload[0]
            and response[2] == 0x78
        ):
            print("  response pending, continuing to wait")
            continue
        return response

    raise TimeoutError(f"Timed out waiting for final response to {label}")


def positive(response: bytes, prefix: bytes) -> bool:
    return len(response) >= len(prefix) and response[: len(prefix)] == prefix


def print_manual_finish() -> None:
    print(
        "\nKOER completed. STOPPING diagnostic traffic.\n\n"
        "Do this now while the engine is still running:\n"
        "  1. Keep the driver's door CLOSED.\n"
        "  2. Wrap the plastic head of the blade key tightly in several layers of aluminum foil.\n"
        "     Leave the metal blade exposed so you can turn it.\n"
        "  3. Turn the ignition fully OFF.\n"
        "  4. Quickly turn the key back to RUN/ON, DO NOT crank/start the engine.\n"
        "  5. On the cluster: Settings -> MyKey -> Clear MyKeys / Clear All MyKeys.\n"
        "  6. Hold OK until the cluster confirms the MyKeys are cleared.\n"
        "  7. Remove the foil and cycle the ignition normally.\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guarded PCM KOER On-Demand Self-Test runner for the 2013 Focus/CANable lab"
    )
    parser.add_argument("--execute", action="store_true", help="transmit the KOER sequence")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=180.0,
        help="maximum time to poll KOER results (default: 180)",
    )
    args = parser.parse_args()

    print("Ford PCM KOER runner")
    print(f"PCM addressing: 0x{PCM_TX_ID:X} -> 0x{PCM_RX_ID:X}")
    print("Planned diagnostic payloads:")
    print("  10 03        enter UDS extended diagnostic session")
    print("  31 01 02 02  start Ford On-Demand Self-Test RID 0x0202")
    print("  31 03 02 02  request self-test results while active")
    print("  3E 00        TesterPresent only while waiting")
    print("No PATS, SecurityAccess, writes, resets, flashing, As-Built, or key programming.\n")

    if not args.execute:
        print("DRY RUN ONLY. Nothing transmitted. Re-run with --execute when ready.")
        return

    print("BEFORE CONTINUING:")
    print("  - vehicle is OUTDOORS or otherwise safely ventilated")
    print("  - engine is already RUNNING")
    print("  - vehicle is stationary")
    print("  - automatic: Park; manual: Neutral")
    print("  - parking brake set")
    print("  - foot off accelerator/brake unless the vehicle itself prompts otherwise")
    print("  - driver's door closed")
    print("KOER may change idle speed or briefly rev the engine.\n")

    confirmation = input("Type KOER to transmit the diagnostic routine: ").strip()
    if confirmation != "KOER":
        raise SystemExit("Cancelled. Nothing transmitted.")

    port = find_port()
    print(f"\nCANable: {port}")

    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=5.5)

        session = request_wait_pending(
            client, EXTENDED_SESSION, label="extended diagnostic session", overall_timeout=10.0
        )
        if not positive(session, bytes.fromhex("50 03")):
            if session[:1] == b"\x7F":
                raise SystemExit(f"Extended session rejected: {describe_negative(session)}")
            raise SystemExit(f"Unexpected extended-session response: {fmt(session)}")

        start = request_wait_pending(
            client, KOER_START, label="start On-Demand Self-Test RID 0x0202", overall_timeout=45.0
        )
        if not positive(start, bytes.fromhex("71 01 02 02")):
            if start[:1] == b"\x7F":
                raise SystemExit(f"KOER start rejected: {describe_negative(start)}")
            raise SystemExit(f"Unexpected KOER-start response: {fmt(start)}")

        print("\nKOER start accepted. Monitoring results...")
        deadline = time.monotonic() + args.max_seconds
        last_tester_present = 0.0

        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_tester_present >= 2.0:
                tp = request_wait_pending(
                    client, TESTER_PRESENT, label="TesterPresent", overall_timeout=8.0
                )
                if not positive(tp, bytes.fromhex("7E 00")):
                    if tp[:1] == b"\x7F":
                        raise SystemExit(f"TesterPresent rejected: {describe_negative(tp)}")
                    raise SystemExit(f"Unexpected TesterPresent response: {fmt(tp)}")
                last_tester_present = time.monotonic()

            result = request_wait_pending(
                client, KOER_RESULTS, label="request KOER results", overall_timeout=20.0
            )
            if result[:1] == b"\x7F":
                # 0x78 is consumed by request_wait_pending; any other NRC is terminal.
                raise SystemExit(f"KOER result request rejected: {describe_negative(result)}")

            if not positive(result, bytes.fromhex("71 03 02 02")):
                raise SystemExit(f"Unexpected KOER-results response: {fmt(result)}")

            # Ford diagnostic definitions commonly encode RoutineType in the upper
            # nibble and RoutineStatus in the lower nibble of the first byte after
            # the RID: 0=completed, 1=aborted, 2=active. Do not infer anything if
            # the byte is absent or uses an unknown status.
            if len(result) >= 5:
                routine_info = result[4]
                status = routine_info & 0x0F
                print(f"  routineInfo=0x{routine_info:02X}, status={status}")
                if status == 0x00:
                    print_manual_finish()
                    return
                if status == 0x01:
                    raise SystemExit("KOER reports ABORTED. Do not perform the MyKey ignition-cycle step.")
                if status == 0x02:
                    time.sleep(1.0)
                    continue
                raise SystemExit(
                    f"Unknown Ford routine status nibble 0x{status:X}; stopping instead of guessing."
                )

            # A positive result response with no Ford RoutineInfo byte is unusual;
            # stop rather than falsely claiming completion.
            raise SystemExit(
                "KOER results were positively acknowledged but contained no RoutineInfo byte. "
                "Save the raw response and stop instead of guessing."
            )

        raise SystemExit("KOER did not report completion before the timeout. Do not perform the MyKey clear step.")


if __name__ == "__main__":
    main()
