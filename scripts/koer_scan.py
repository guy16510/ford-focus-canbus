#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port

PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
KOER_RID = 0x0282
KOER_START = bytes.fromhex("31 01 02 82")
KOER_RESULTS = bytes.fromhex("31 03 02 82")

# Explicit allowlist only. No arbitrary service/RID scanning.
SESSION_CANDIDATES = (
    ("default", bytes.fromhex("10 01"), bytes.fromhex("50 01")),
    ("extended", bytes.fromhex("10 03"), bytes.fromhex("50 03")),
)

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

PERMANENT_NRCS = {0x11, 0x12, 0x13, 0x31, 0x33, 0x7E, 0x7F}
CONDITION_NRCS = {0x22, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x8B, 0x8F, 0x92, 0x93}

# Standard OBD-II Mode 01 values used only as a read-only preflight.
OBD_PIDS = {
    0x0C: "RPM",
    0x0D: "Speed",
    0x05: "Coolant",
    0x1F: "Runtime",
    0x42: "Voltage",
}


@dataclass
class CandidateState:
    name: str
    session_request: bytes
    session_positive: bytes
    permanent_failure: bool = False
    last_response: bytes | None = None
    attempts: int = 0


class RunLog:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._fh = None
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = path.open("a", encoding="utf-8")

    def write(self, message: str = "") -> None:
        print(message, flush=True)
        if self._fh is not None:
            stamp = datetime.now().astimezone().isoformat(timespec="seconds")
            self._fh.write(f"[{stamp}] {message}\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def fmt(data: bytes) -> str:
    return data.hex(" ").upper()


def nrc_of(response: bytes) -> int | None:
    if len(response) >= 3 and response[0] == 0x7F:
        return response[2]
    return None


def positive(response: bytes, prefix: bytes) -> bool:
    return len(response) >= len(prefix) and response[: len(prefix)] == prefix


def decode_obd(pid: int, response: bytes) -> str | None:
    if len(response) < 3 or response[:2] != bytes((0x41, pid)):
        return None
    data = response[2:]
    if pid == 0x0C and len(data) >= 2:
        return f"{int.from_bytes(data[:2], 'big') / 4:.0f} rpm"
    if pid == 0x0D and data:
        return f"{data[0]} km/h"
    if pid == 0x05 and data:
        return f"{data[0] - 40} C"
    if pid == 0x1F and len(data) >= 2:
        return f"{int.from_bytes(data[:2], 'big')} sec"
    if pid == 0x42 and len(data) >= 2:
        return f"{int.from_bytes(data[:2], 'big') / 1000:.2f} V"
    return None


def numeric_obd(pid: int, response: bytes) -> float | None:
    if len(response) < 3 or response[:2] != bytes((0x41, pid)):
        return None
    data = response[2:]
    if pid == 0x0C and len(data) >= 2:
        return int.from_bytes(data[:2], "big") / 4
    if pid == 0x0D and data:
        return float(data[0])
    if pid == 0x05 and data:
        return float(data[0] - 40)
    if pid == 0x1F and len(data) >= 2:
        return float(int.from_bytes(data[:2], "big"))
    if pid == 0x42 and len(data) >= 2:
        return int.from_bytes(data[:2], "big") / 1000
    return None


def receive_final(
    client: IsoTpClient,
    request_service: int,
    log: RunLog,
    *,
    overall_timeout: float = 30.0,
) -> bytes:
    """Receive one application response, honoring UDS NRC 0x78 without resending."""
    deadline = time.monotonic() + overall_timeout
    while time.monotonic() < deadline:
        try:
            response = client.receive_payload()
        except TimeoutError:
            continue
        log.write(f"RX 0x{PCM_RX_ID:X}: {fmt(response)}")
        if (
            len(response) >= 3
            and response[0] == 0x7F
            and response[1] == request_service
            and response[2] == 0x78
        ):
            log.write("  NRC 0x78 responsePending, waiting for final response")
            continue
        return response
    raise TimeoutError("Timed out waiting for final ECU response")


def exchange(
    client: IsoTpClient,
    payload: bytes,
    log: RunLog,
    *,
    label: str,
    timeout: float = 30.0,
) -> bytes:
    log.write(f"TX 0x{PCM_TX_ID:X}: {fmt(payload)}  [{label}]")
    client.send_payload(payload)
    return receive_final(client, payload[0], log, overall_timeout=timeout)


def exchange_busy_retry(
    client: IsoTpClient,
    payload: bytes,
    log: RunLog,
    *,
    label: str,
    max_busy_retries: int = 5,
    timeout: float = 30.0,
) -> bytes:
    busy = 0
    while True:
        response = exchange(client, payload, log, label=label, timeout=timeout)
        if nrc_of(response) != 0x21:
            return response
        busy += 1
        if busy > max_busy_retries:
            return response
        log.write(f"  busyRepeatRequest, retry {busy}/{max_busy_retries} in 0.75 sec")
        time.sleep(0.75)


def enter_session(client: IsoTpClient, candidate: CandidateState, log: RunLog) -> tuple[bool, bytes]:
    response = exchange_busy_retry(
        client,
        candidate.session_request,
        log,
        label=f"enter {candidate.name} diagnostic session",
        timeout=10.0,
    )
    if positive(response, candidate.session_positive):
        return True, response
    return False, response


def read_preflight(client: IsoTpClient, log: RunLog) -> dict[int, float | None]:
    log.write("\n--- READ-ONLY LIVE PRECHECK ---")
    values: dict[int, float | None] = {}
    for pid, name in OBD_PIDS.items():
        request = bytes((0x01, pid))
        try:
            response = exchange(client, request, log, label=f"OBD {name}", timeout=4.0)
        except TimeoutError:
            log.write(f"{name:9}: UNKNOWN (timeout)")
            values[pid] = None
            continue
        rendered = decode_obd(pid, response)
        value = numeric_obd(pid, response)
        values[pid] = value
        if rendered is None:
            nrc = nrc_of(response)
            if nrc is not None:
                log.write(f"{name:9}: UNKNOWN, NRC 0x{nrc:02X} {NRC.get(nrc, 'unknown')}")
            else:
                log.write(f"{name:9}: UNKNOWN ({fmt(response)})")
        else:
            log.write(f"{name:9}: {rendered}")

    rpm = values.get(0x0C)
    speed = values.get(0x0D)
    if rpm is not None and rpm <= 0:
        raise RuntimeError("Engine is not running. Start it normally before KOER scan.")
    if speed is not None and speed != 0:
        raise RuntimeError("Vehicle speed is not zero. Stop the vehicle before KOER scan.")
    if rpm is None:
        log.write("WARNING: RPM unavailable, engine-running state cannot be confirmed from OBD.")
    if speed is None:
        log.write("WARNING: speed unavailable, stationary state cannot be confirmed from OBD.")
    return values


def describe_nrc(response: bytes) -> str:
    code = nrc_of(response)
    if code is None:
        return f"unexpected response {fmt(response)}"
    return f"NRC 0x{code:02X} {NRC.get(code, 'unknown')}"


def try_start_candidate(
    client: IsoTpClient,
    candidate: CandidateState,
    log: RunLog,
) -> str:
    """Return accepted, transient, or permanent."""
    candidate.attempts += 1
    log.write(f"\n=== CANDIDATE: {candidate.name} session / RID 0x{KOER_RID:04X} ===")

    ok, session_response = enter_session(client, candidate, log)
    if not ok:
        candidate.last_response = session_response
        code = nrc_of(session_response)
        log.write(f"Session rejected: {describe_nrc(session_response)}")
        if code in PERMANENT_NRCS:
            candidate.permanent_failure = True
            return "permanent"
        return "transient"

    response = exchange_busy_retry(
        client,
        KOER_START,
        log,
        label="start Ford Key-On Engine Running Self Test RID 0x0282",
        timeout=30.0,
    )
    candidate.last_response = response

    if positive(response, bytes.fromhex("71 01 02 82")):
        log.write("\n**************************************************")
        log.write("KOER START ACCEPTED")
        log.write("LEAVE THE ENGINE RUNNING")
        log.write("DO NOT WRAP THE KEY YET")
        log.write("**************************************************")
        return "accepted"

    code = nrc_of(response)
    log.write(f"KOER start rejected: {describe_nrc(response)}")
    if code in PERMANENT_NRCS:
        candidate.permanent_failure = True
        return "permanent"
    if code in CONDITION_NRCS or code == 0x21:
        return "transient"

    # Unknown response: never improvise a new command.
    candidate.permanent_failure = True
    return "permanent"


def decode_ford_routine_status(result_data: bytes) -> str:
    """
    Conservative Ford-family status decoder.

    Ford diagnostic definitions for self-test routines use completed/aborted/active
    status values. This PCM's exact 0x0282 result record is not yet proven, so only
    recognize the small status pattern already seen in Ford family metadata.
    """
    if not result_data:
        return "no-status"
    status = result_data[0] & 0x0F
    if status == 0:
        return "completed"
    if status == 1:
        return "aborted"
    if status == 2:
        return "active"
    return "unknown"


def poll_koer_results(client: IsoTpClient, log: RunLog, *, max_seconds: float = 90.0) -> bool:
    log.write("\nKOER accepted. Polling the SAME routine for results.")
    deadline = time.monotonic() + max_seconds
    no_status_positive = 0

    while time.monotonic() < deadline:
        try:
            response = exchange_busy_retry(
                client,
                KOER_RESULTS,
                log,
                label="request KOER RID 0x0282 results",
                timeout=30.0,
            )
        except TimeoutError:
            log.write("Result request timed out; retrying same result request in 1 sec")
            time.sleep(1.0)
            continue

        if positive(response, bytes.fromhex("71 03 02 82")):
            result_data = response[4:]
            status = decode_ford_routine_status(result_data)
            log.write(f"KOER result data: {fmt(result_data) if result_data else '<none>'}")
            log.write(f"KOER interpreted status: {status}")

            if status == "completed":
                return True
            if status == "aborted":
                log.write("KOER reported aborted. Not starting foil procedure.")
                return False
            if status == "active":
                time.sleep(1.0)
                continue
            if status == "no-status":
                # A repeated positive requestRoutineResults with no active/aborted
                # marker is treated as result availability, but require two identical
                # positive reads before proceeding.
                no_status_positive += 1
                if no_status_positive >= 2:
                    log.write("Two positive KOER result responses returned with no status bytes.")
                    log.write("Treating the routine result as available/completed.")
                    return True
                time.sleep(1.0)
                continue

            log.write("Unknown KOER result format; continuing to poll the same routine only.")
            time.sleep(1.0)
            continue

        code = nrc_of(response)
        if code in (0x21, 0x22) or code in CONDITION_NRCS:
            log.write(f"Result not ready/condition changed: {describe_nrc(response)}")
            time.sleep(1.0)
            continue
        if code == 0x33:
            log.write("SecurityAccess denied. Stopping. SecurityAccess will NOT be attempted.")
            return False

        log.write(f"Unexpected terminal KOER result response: {fmt(response)}")
        return False

    log.write("Timed out waiting for an unambiguous KOER completion result.")
    return False


def foil_wizard(log: RunLog) -> None:
    log.write("\n**************************************************")
    log.write("KOER COMPLETE")
    log.write("LEAVE THE ENGINE RUNNING")
    log.write("ACTIVE CAN TRANSMISSIONS HAVE STOPPED")
    log.write("**************************************************")
    log.write("")
    log.write("Keep the driver's door CLOSED.")
    log.write("Wrap the PLASTIC HEAD of the key tightly in several layers of foil.")
    log.write("Leave the metal blade exposed. Keep the engine RUNNING while wrapping it.")
    input("\nPress ENTER after the key head is wrapped and the engine is still running: ")

    print("\nTURN THE IGNITION FULLY OFF NOW. Keep the door CLOSED and foil ON.", flush=True)
    input("Press ENTER as soon as ignition is fully OFF: ")

    print("\nIMMEDIATELY TURN THE KEY TO RUN/ON. DO NOT CRANK. DO NOT START THE ENGINE.", flush=True)
    print("'No key detected' is expected while the transponder is blocked.", flush=True)
    input("Press ENTER once ignition is in RUN/ON and the engine is NOT running: ")

    print("\nInstrument cluster: Settings -> MyKey -> Clear MyKeys / Clear All MyKeys", flush=True)
    print("Hold OK until the cluster explicitly confirms all MyKeys were cleared.", flush=True)
    result = input("Type CLEARED only after the cluster confirms it, or anything else to stop: ").strip().upper()
    if result != "CLEARED":
        print("\nClear was not confirmed. Remove foil before attempting a normal restart.", flush=True)
        return

    print("\nRemove the foil. Turn ignition OFF. Wait a few seconds, then start normally.", flush=True)
    print("Verify the MyKey count/restrictions are gone.", flush=True)


def dry_run() -> int:
    print("Ford Focus interactive KOER scanner - DRY RUN")
    print("\nNO CAN FRAMES WILL BE TRANSMITTED.\n")
    print("Safe candidate matrix:")
    print("  1. 10 01 -> expect 50 01 -> 31 01 02 82")
    print("  2. 10 03 -> expect 50 03 -> 31 01 02 82")
    print("\nIf start is accepted:")
    print("  poll 31 03 02 82 only")
    print("  wait through NRC 0x78 without resending the start")
    print("  retry NRC 0x21 with delay")
    print("  retry condition NRCs after delay")
    print("\nKnown failed requests are NOT retransmitted:")
    print("  31 01 02 02")
    print("  31 02 00")
    print("  31 82")
    print("\nNever attempted: PATS, SecurityAccess, writes, resets, programming, flashing, arbitrary RIDs.")
    print("\nThe foil procedure is shown only after positive KOER completion.")
    return 0


def run(args: argparse.Namespace) -> int:
    if not args.execute:
        return dry_run()

    print("\nFORD FOCUS INTERACTIVE KOER SCANNER")
    print("===================================")
    print("START THE CAR NORMALLY AND LEAVE THE ENGINE RUNNING")
    print("PARK, PARKING BRAKE, VEHICLE STATIONARY")
    print("DRIVER'S DOOR CLOSED")
    print("NO FOIL YET")
    print("\nThis scanner only tries the allowlisted Ford KOER RID 0x0282 in")
    print("default and extended diagnostic sessions. It will not brute-force the ECU.")
    confirm = input("\nType SCAN to begin: ").strip().upper()
    if confirm != "SCAN":
        print("Cancelled. Nothing transmitted.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = RunLog(Path("logs") / f"koer_scan_{stamp}.log")
    candidates = [CandidateState(name, request, positive_prefix) for name, request, positive_prefix in SESSION_CANDIDATES]

    try:
        port = args.port or find_port()
        log.write(f"CANable: {port}")
        log.write(f"PCM: 0x{PCM_TX_ID:X} -> 0x{PCM_RX_ID:X}")
        log.write(f"KOER RID: 0x{KOER_RID:04X}")

        with Slcan(port) as bus:
            bus.open_channel(500000, silent=False)
            client = IsoTpClient(bus, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=2.0)

            cycle = 0
            while True:
                cycle += 1
                log.write(f"\n################ SCAN CYCLE {cycle} ################")

                # Return to the known-safe default session before OBD preflight.
                try:
                    default_response = exchange_busy_retry(
                        client,
                        bytes.fromhex("10 01"),
                        log,
                        label="default session before preflight",
                        timeout=10.0,
                    )
                    if not positive(default_response, bytes.fromhex("50 01")):
                        log.write(f"Default session preflight request was not accepted: {describe_nrc(default_response)}")
                except TimeoutError:
                    log.write("Default session request timed out; attempting read-only preflight anyway.")

                values = read_preflight(client, log)
                coolant = values.get(0x05)
                if coolant is not None:
                    log.write(f"NOTE: coolant is {coolant:.0f} C; no unverified KOER threshold is enforced.")

                active = [candidate for candidate in candidates if not candidate.permanent_failure]
                if not active:
                    log.write("\nAll allowlisted KOER candidates have permanent failures. Stopping safely.")
                    break

                transient_seen = False
                for candidate in active:
                    outcome = try_start_candidate(client, candidate, log)
                    if outcome == "accepted":
                        completed = poll_koer_results(client, log, max_seconds=args.result_timeout)
                        if completed:
                            # Exit the with-block first so the CAN channel is closed before foil steps.
                            log.write("KOER completion detected. Closing CAN channel before manual steps.")
                            bus.shutdown()
                            foil_wizard(log)
                            return 0
                        log.write("KOER started but completion was not positively established. Stopping without foil.")
                        return 2
                    if outcome == "transient":
                        transient_seen = True

                if all(candidate.permanent_failure for candidate in candidates):
                    log.write("\nBoth allowlisted sessions permanently rejected KOER RID 0x0282.")
                    break

                if args.max_cycles and cycle >= args.max_cycles:
                    log.write(f"\nReached max scan cycles ({args.max_cycles}). Stopping safely.")
                    break

                if transient_seen:
                    log.write(f"\nOnly transient/condition failures remain. Retrying in {args.retry_delay:.1f} sec.")
                else:
                    log.write(f"\nRetrying remaining safe candidates in {args.retry_delay:.1f} sec.")
                time.sleep(args.retry_delay)

        log.write("\nKOER was not entered/completed. DO NOT START THE FOIL PROCEDURE.")
        log.write("\nSummary:")
        for candidate in candidates:
            state = "PERMANENT" if candidate.permanent_failure else "RETRYABLE"
            last = "none" if candidate.last_response is None else fmt(candidate.last_response)
            log.write(f"  {candidate.name}: {state}, attempts={candidate.attempts}, last={last}")
        return 1

    except KeyboardInterrupt:
        log.write("\nCtrl-C received. Stopping CAN transmissions immediately.")
        log.write("DO NOT START THE FOIL PROCEDURE unless KOER completion was already confirmed.")
        return 130
    except Exception as error:
        log.write(f"\nSTOPPED SAFELY: {error}")
        log.write("DO NOT START THE FOIL PROCEDURE.")
        return 2
    finally:
        log.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive, allowlisted Ford Focus PCM KOER scanner")
    parser.add_argument("--execute", action="store_true", help="actually transmit CAN diagnostic requests")
    parser.add_argument("--port", help="CANable serial port; defaults to CANABLE_PORT/autodetection")
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="maximum retry cycles; 0 means continue until success, permanent failure, or Ctrl-C",
    )
    parser.add_argument("--retry-delay", type=float, default=3.0, help="seconds between retry cycles")
    parser.add_argument("--result-timeout", type=float, default=90.0, help="seconds to poll KOER result after start")
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
