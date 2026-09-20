#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from collections.abc import Callable

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


def render_failure(label: str, response: bytes) -> str:
    detail = describe_negative(response) if response[:1] == b"\x7f" else "status=unexpected"
    return (
        "\nKOER DID NOT COMPLETE. DO NOT PERFORM THE FOIL/MYKEY PROCEDURE.\n"
        f"Final {label} response: RX 0x{PCM_RX_ID:X}: {fmt(response)} ({detail})"
    )


def manual_wizard(
    *, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print
) -> bool:
    def pause(prompt: str) -> None:
        input_fn(prompt)

    output_fn("\n==================================================\nKOER COMPLETE - LEAVE THE ENGINE RUNNING\n==================================================")
    output_fn("DO NOT TURN THE KEY OFF YET.\n")
    output_fn("Now get several layers of aluminum foil ready.\nWrap the PLASTIC HEAD of the ignition key tightly in foil.\nCover the transponder area completely.\nLeave the METAL BLADE exposed so the key can still turn.\n\nThe engine must STILL BE RUNNING while you wrap the key.\nDriver's door MUST remain CLOSED.")
    pause("Press ENTER ONLY AFTER the key head is wrapped, the engine is still running, and the driver's door is still closed: ")

    output_fn("\n==================================================\nSTEP 2 - TURN ENGINE OFF\n==================================================\nTurn the ignition fully to OFF now.\nDO NOT open the driver's door, remove the foil, or remove the key unless absolutely required.\nDriver's door MUST remain CLOSED.")
    pause("Press ENTER as soon as the ignition is fully OFF: ")

    output_fn("\n==================================================\nSTEP 3 - TURN KEY BACK TO RUN/ON\n==================================================\nImmediately turn the key forward to RUN/ON.\nIMPORTANT: DO NOT CRANK THE ENGINE. DO NOT START THE ENGINE.\nLeave the foil on the key. Driver's door MUST remain CLOSED.")
    pause("Press ENTER once the ignition is in RUN/ON and the engine is NOT running: ")

    output_fn("\n==================================================\nSTEP 4 - CLEAR MYKEY\n==================================================\nOn the instrument cluster:\nSettings\n-> MyKey\n-> Clear MyKeys / Clear All MyKeys\n\nSelect it and HOLD OK until the cluster confirms that all MyKeys have been cleared.\nDo not remove the foil yet.")
    cluster_confirmation = input_fn(
        "Press ENTER after the cluster explicitly confirms the MyKeys were cleared (type NO if it did not): "
    ).strip().lower()
    if cluster_confirmation == "no":
        output_fn(
            "\nThe cluster did not confirm that MyKeys were cleared. The attempt did not succeed.\n"
            "Save the exact terminal log; do not try random diagnostic commands."
        )
        return False

    output_fn("\n==================================================\nSTEP 5 - FINISH\n==================================================\nRemove the foil from the key.\nTurn the ignition OFF.\nWait a few seconds.\nStart the vehicle normally.\nVerify the MyKey warning/count and restrictions are gone, and the key behaves as an unrestricted/admin key.")
    pause("Press ENTER when finished: ")
    output_fn("\nProcedure complete.\nIf the cluster never offered Clear MyKeys, the attempt did not succeed. Save the exact terminal log; do not try random diagnostic commands.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Guarded PCM KOER On-Demand Self-Test runner for the 2013 Focus/CANable lab"
    )
    parser.add_argument("--execute", action="store_true", help="transmit the KOER sequence")
    parser.add_argument(
        "--dry-run", action="store_true", help="preview the complete wizard without CAN traffic"
    )
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=180.0,
        help="maximum time to poll KOER results (default: 180)",
    )
    args = parser.parse_args()
    if args.execute and args.dry_run:
        parser.error("--execute and --dry-run cannot be used together")

    print("Ford PCM KOER runner")
    print(f"PCM addressing: 0x{PCM_TX_ID:X} -> 0x{PCM_RX_ID:X}")
    print("Planned diagnostic payloads:")
    print("  10 03        enter UDS extended diagnostic session")
    print("  31 01 02 02  start Ford On-Demand Self-Test RID 0x0202")
    print("  31 03 02 02  request self-test results while active")
    print("  3E 00        TesterPresent only while waiting")
    print("No PATS, SecurityAccess, writes, resets, flashing, As-Built, or key programming.\n")

    print("\n## BEFORE WE START\n")
    print("1. Move the car OUTSIDE or ensure very good ventilation.\n2. Connect CANable.\n3. Start the engine normally using the MyKey.\n4. Keep the driver's door CLOSED.\n5. Vehicle stationary.\n6. Automatic transmission in PARK, or manual in NEUTRAL.\n7. Parking brake set.\n8. DO NOT put foil on the key yet.\n")
    if args.dry_run or not args.execute:
        print("DRY RUN ONLY - NOTHING WILL BE TRANSMITTED.")
    else:
        print('Leave the engine RUNNING. Press ENTER when all of the above is true.')
    input("Press ENTER to continue: ")
    print("\nExact CAN requests that may be transmitted:")
    print("  10 03        enter UDS extended diagnostic session")
    print("  31 01 02 02  start Ford On-Demand Self-Test RID 0x0202")
    print("  31 03 02 02  request self-test results while active")
    print("  3E 00        TesterPresent only while waiting")
    confirmation = input("Type KOER to transmit the diagnostic routine: ").strip()
    if confirmation != "KOER":
        raise SystemExit("Cancelled. Nothing transmitted.")

    if not args.execute:
        print("\nDRY RUN ONLY - the diagnostic sequence was previewed; nothing was transmitted.")
        manual_wizard()
        return

    port = find_port()
    print(f"\nCANable: {port}")

    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=5.5)

        session = request_wait_pending(
            client, EXTENDED_SESSION, label="extended diagnostic session", overall_timeout=10.0
        )
        if not positive(session, bytes.fromhex("50 03")):
            raise SystemExit(render_failure("extended-session", session))

        start = request_wait_pending(
            client, KOER_START, label="start On-Demand Self-Test RID 0x0202", overall_timeout=45.0
        )
        if not positive(start, bytes.fromhex("71 01 02 02")):
            if start[:1] == b"\x7F":
                raise SystemExit(render_failure("KOER-start", start))
            raise SystemExit(render_failure("KOER-start", start))

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
                        raise SystemExit(render_failure("TesterPresent", tp))
                    raise SystemExit(render_failure("TesterPresent", tp))
                last_tester_present = time.monotonic()

            result = request_wait_pending(
                client, KOER_RESULTS, label="request KOER results", overall_timeout=20.0
            )
            if result[:1] == b"\x7F":
                # 0x78 is consumed by request_wait_pending; any other NRC is terminal.
                raise SystemExit(render_failure("KOER-results", result))

            if not positive(result, bytes.fromhex("71 03 02 02")):
                raise SystemExit(render_failure("KOER-results", result))

            # Ford diagnostic definitions commonly encode RoutineType in the upper
            # nibble and RoutineStatus in the lower nibble of the first byte after
            # the RID: 0=completed, 1=aborted, 2=active. Do not infer anything if
            # the byte is absent or uses an unknown status.
            if len(result) >= 5:
                routine_info = result[4]
                status = routine_info & 0x0F
                print(f"  routineInfo=0x{routine_info:02X}, status={status}")
                if status == 0x00:
                    can.close_channel()
                    print("\nKOER completed. Stopping diagnostic traffic before the manual procedure.")
                    manual_wizard()
                    return
                if status == 0x01:
                    raise SystemExit(render_failure("KOER-results", result) + "\nInterpreted status: ABORTED")
                if status == 0x02:
                    time.sleep(1.0)
                    continue
                raise SystemExit(
                    render_failure("KOER-results", result) + f"\nInterpreted status: UNKNOWN (0x{status:X})"
                )

            # A positive result response with no Ford RoutineInfo byte is unusual;
            # stop rather than falsely claiming completion.
            raise SystemExit(
                render_failure("KOER-results", result)
            )

        raise SystemExit("\nKOER DID NOT COMPLETE. DO NOT PERFORM THE FOIL/MYKEY PROCEDURE.\nTimed out waiting for final result.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSTOPPED by Ctrl-C. CAN traffic was closed; no foil/ignition procedure should be performed.")
