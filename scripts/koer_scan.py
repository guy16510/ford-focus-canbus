#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port

PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
KOER_RID = 0x0282
KOER_START = bytes.fromhex("31 01 02 82")
KOER_RESULTS = bytes.fromhex("31 03 02 82")
KOER_START_POSITIVE = bytes.fromhex("71 01 02 82")
KOER_RESULTS_POSITIVE = bytes.fromhex("71 03 02 82")

# Ford FDRS diagnostic definitions identify 0x0282 as "Key-On Engine Running
# Self Test" and model the routine as a Type-2 UDS RoutineControl routine.
SESSION_CANDIDATES = (
    ("extended", bytes.fromhex("10 03"), bytes.fromhex("50 03")),
    ("default", bytes.fromhex("10 01"), bytes.fromhex("50 01")),
)

NRC = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat",
    0x14: "responseTooLong",
    0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect",
    0x24: "requestSequenceError",
    0x25: "noResponseFromSubnetComponent",
    0x26: "failurePreventsExecutionOfRequestedAction",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x36: "exceedNumberOfAttempts",
    0x37: "requiredTimeDelayNotExpired",
    0x70: "uploadDownloadNotAccepted",
    0x71: "transferDataSuspended",
    0x72: "generalProgrammingFailure",
    0x73: "wrongBlockSequenceCounter",
    0x78: "requestCorrectlyReceivedResponsePending",
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
    0x89: "vehicleSpeedTooLow",
    0x8A: "throttlePedalTooHigh",
    0x8B: "throttlePedalTooLow",
    0x8C: "transmissionRangeNotInNeutral",
    0x8D: "transmissionRangeNotInGear",
    0x8F: "brakeSwitchesNotClosed",
    0x90: "shifterLeverNotInPark",
    0x91: "torqueConverterClutchLocked",
    0x92: "voltageTooHigh",
    0x93: "voltageTooLow",
}

SESSION_MISMATCH_NRCS = {0x7E, 0x7F}
CONDITION_NRCS = {
    0x22,
    0x81,
    0x82,
    0x83,
    0x84,
    0x85,
    0x86,
    0x87,
    0x88,
    0x89,
    0x8A,
    0x8B,
    0x8C,
    0x8D,
    0x8F,
    0x90,
    0x91,
    0x92,
    0x93,
}
TERMINAL_START_NRCS = {0x11, 0x12, 0x13, 0x24, 0x26, 0x31, 0x33, 0x35, 0x36, 0x37}

OBD_PIDS = {
    0x0C: "RPM",
    0x0D: "Speed",
    0x05: "Coolant",
    0x1F: "Runtime",
    0x42: "Voltage",
}

TYPE2_STATUS = {
    0x0: "completed",
    0x1: "aborted",
    0x2: "active",
}


@dataclass
class CandidateState:
    name: str
    session_request: bytes
    session_positive: bytes
    session_mismatch: bool = False
    last_response: bytes | None = None
    attempts: int = 0


@dataclass(frozen=True)
class Type2RoutineInfo:
    raw: int
    routine_type: int
    status_code: int
    status: str
    on_demand_dtc: bytes | None
    extra: bytes


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


def describe_nrc(response: bytes) -> str:
    code = nrc_of(response)
    if code is None:
        return f"unexpected response {fmt(response)}"
    return f"NRC 0x{code:02X} {NRC.get(code, 'unknown')}"


def decode_type2_routine_info(data: bytes, *, expect_dtc: bool = False) -> Type2RoutineInfo:
    """Decode Ford Type-2 RoutineInfo.

    Ford FDRS metadata defines one RoutineInfo byte for Type-2 self tests:
      bits 7..4: RoutineType = 0x2
      bits 3..0: 0 completed, 1 aborted, 2 active

    For 0x0282 requestRoutineResults, Ford metadata additionally defines three
    bytes of On-Demand DTC data. Completion is not accepted if that required
    result record is absent.
    """
    if not data:
        raise ValueError("missing Ford RoutineInfo byte")

    raw = data[0]
    routine_type = raw >> 4
    status_code = raw & 0x0F
    if routine_type != 0x2:
        raise ValueError(
            f"unexpected RoutineType 0x{routine_type:X} in RoutineInfo 0x{raw:02X}; expected Type 2"
        )
    if status_code not in TYPE2_STATUS:
        raise ValueError(
            f"unknown Type-2 RoutineStatus 0x{status_code:X} in RoutineInfo 0x{raw:02X}"
        )

    dtc: bytes | None = None
    extra = b""
    if len(data) >= 4:
        dtc = data[1:4]
        extra = data[4:]
    elif expect_dtc and status_code == 0x0:
        raise ValueError(
            "completed KOER result is missing Ford's 3-byte On-Demand DTC result record"
        )
    else:
        extra = data[1:]

    return Type2RoutineInfo(
        raw=raw,
        routine_type=routine_type,
        status_code=status_code,
        status=TYPE2_STATUS[status_code],
        on_demand_dtc=dtc,
        extra=extra,
    )


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
    p2_star_seconds: float = 6.0,
) -> bytes:
    """Receive final UDS response; 0x78 means wait without resending."""
    overall_deadline = time.monotonic() + overall_timeout
    pending_deadline: float | None = None

    while time.monotonic() < overall_deadline:
        try:
            response = client.receive_payload()
        except TimeoutError:
            if pending_deadline is not None and time.monotonic() >= pending_deadline:
                raise TimeoutError("P2* expired waiting for final ECU response after NRC 0x78")
            continue

        log.write(f"RX 0x{PCM_RX_ID:X}: {fmt(response)}")
        if (
            len(response) >= 3
            and response[0] == 0x7F
            and response[1] == request_service
            and response[2] == 0x78
        ):
            pending_deadline = min(overall_deadline, time.monotonic() + p2_star_seconds)
            log.write(
                f"  NRC 0x78 responsePending; waiting up to {p2_star_seconds:.1f}s P2* for next response"
            )
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
    """Retry 0x21 only where an immediate bounded retry is appropriate.

    KOER result polling deliberately calls this with max_busy_retries=0 because
    live vehicle evidence shows 0x21 is the PCM saying the already-active KOER
    routine is still busy. Hammering six result requests every poll adds noise
    and does not make the routine finish faster.
    """
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
    return positive(response, candidate.session_positive), response


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
            code = nrc_of(response)
            if code is not None:
                log.write(f"{name:9}: UNKNOWN, {describe_nrc(response)}")
            else:
                log.write(f"{name:9}: UNKNOWN ({fmt(response)})")
        else:
            log.write(f"{name:9}: {rendered}")

    rpm = values.get(0x0C)
    speed = values.get(0x0D)
    if rpm is not None and rpm <= 0:
        raise RuntimeError("Engine is not running. Start it normally before KOER.")
    if speed is not None and speed != 0:
        raise RuntimeError("Vehicle speed is not zero. Stop the vehicle before KOER.")
    if rpm is None:
        log.write("WARNING: RPM unavailable, engine-running state cannot be confirmed from OBD.")
    if speed is None:
        log.write("WARNING: speed unavailable, stationary state cannot be confirmed from OBD.")
    return values


def log_type2_info(log: RunLog, info: Type2RoutineInfo, *, context: str) -> None:
    log.write(
        f"{context}: RoutineInfo=0x{info.raw:02X} "
        f"(Type={info.routine_type}, status={info.status})"
    )
    if info.on_demand_dtc is not None:
        raw = fmt(info.on_demand_dtc)
        if info.on_demand_dtc == b"\x00\x00\x00":
            log.write(f"{context}: On-Demand DTC raw={raw} (zero value)")
        else:
            log.write(f"{context}: On-Demand DTC raw={raw} (left undecoded)")
    if info.extra:
        log.write(f"{context}: unexpected extra response bytes={fmt(info.extra)}")


def print_operator_actions(
    log: RunLog,
    *,
    coolant_c: float | None = None,
    runtime_s: float | None = None,
) -> None:
    """Show the driver actions Ford service material associates with KOER.

    Not every Ford powertrain uses every input. These actions are intentionally
    conservative and do not automate vehicle controls.
    """
    log.write("\n==================================================")
    log.write("KOER IS ACTIVE - PERFORM THE DRIVER INPUTS ONCE NOW")
    log.write("==================================================")
    log.write("Keep the vehicle stopped in PARK with the parking brake set.")
    log.write("1. Briefly press and release the BRAKE pedal.")
    log.write("2. If safe, turn the steering wheel at least about 1/4 turn, then return toward center.")
    log.write("3. If your car has an OD/transmission-control switch, cycle it once. If it does not, skip this.")
    log.write("4. If the test is still busy after ~15 seconds, give the accelerator ONE brief, light-to-moderate press and release.")
    log.write("   Do NOT hold the accelerator down and do NOT race the engine.")
    log.write("")
    log.write("Ford service material specifies KOER at normal operating temperature.")
    if coolant_c is not None:
        log.write(f"Current coolant reading before KOER was {coolant_c:.0f} C.")
    if runtime_s is not None:
        log.write(f"Engine runtime before KOER was {runtime_s:.0f} sec.")
    log.write("If the temperature gauge is not yet in its normal range, leave the engine running and let it continue warming.")
    log.write("No key foil yet. Do not turn the engine off. The script will keep checking for completion automatically.")
    log.write("==================================================\n")


def try_start_candidate(
    client: IsoTpClient,
    candidate: CandidateState,
    log: RunLog,
) -> tuple[str, Type2RoutineInfo | None]:
    """Try one evidence-backed KOER candidate."""
    candidate.attempts += 1
    log.write(f"\n=== CANDIDATE: {candidate.name} session / RID 0x{KOER_RID:04X} ===")

    ok, session_response = enter_session(client, candidate, log)
    if not ok:
        candidate.last_response = session_response
        log.write(f"Session rejected: {describe_nrc(session_response)}")
        return "terminal", None

    response = exchange_busy_retry(
        client,
        KOER_START,
        log,
        label="start Ford Key-On Engine Running Self Test RID 0x0282",
        timeout=45.0,
    )
    candidate.last_response = response

    if positive(response, KOER_START_POSITIVE):
        try:
            info = decode_type2_routine_info(response[4:])
        except ValueError as error:
            log.write(f"Positive KOER start had invalid Ford Type-2 data: {error}")
            return "terminal", None

        log_type2_info(log, info, context="KOER start")
        if info.status == "active":
            log.write("\n**************************************************")
            log.write("KOER START ACCEPTED - ROUTINE IS ACTIVE")
            log.write("LEAVE THE ENGINE RUNNING")
            log.write("DO NOT WRAP THE KEY YET")
            log.write("**************************************************")
            return "accepted", info
        if info.status == "completed":
            log.write("KOER start response already reports completed; requesting results for confirmation.")
            return "completed", info
        if info.status == "aborted":
            log.write("KOER start response reports aborted. No foil procedure will start.")
            return "aborted", info
        return "terminal", info

    code = nrc_of(response)
    log.write(f"KOER start rejected: {describe_nrc(response)}")
    if code in SESSION_MISMATCH_NRCS:
        candidate.session_mismatch = True
        return "wrong-session", None
    if code in CONDITION_NRCS:
        return "condition", None
    if code in TERMINAL_START_NRCS or code is None:
        return "terminal", None
    return "terminal", None


def poll_koer_results(
    client: IsoTpClient,
    log: RunLog,
    *,
    max_seconds: float = 300.0,
    poll_interval: float = 2.5,
) -> bool:
    """Poll RID 0x0282 until Ford Type-2 completion is explicit.

    Live 2013 Focus evidence: once 31 01 02 82 returned 71 01 02 82 22,
    immediate result requests returned 7F 31 21 repeatedly. That is not a
    failed start; it is busyRepeatRequest while the accepted KOER routine is
    still active. Send only one result request per poll interval.
    """
    log.write("\nPolling the SAME KOER routine for Ford Type-2 results.")
    log.write("NRC 0x21 while polling means the PCM is busy with the already-active KOER test; it is not a failed start.")
    deadline = time.monotonic() + max_seconds
    started = time.monotonic()
    busy_count = 0
    last_reminder = started

    while time.monotonic() < deadline:
        try:
            response = exchange_busy_retry(
                client,
                KOER_RESULTS,
                log,
                label="request KOER RID 0x0282 results",
                max_busy_retries=0,
                timeout=30.0,
            )
        except TimeoutError as error:
            log.write(f"KOER result wait timed out: {error}; waiting before the next result request")
            time.sleep(poll_interval)
            continue

        if positive(response, KOER_RESULTS_POSITIVE):
            try:
                info = decode_type2_routine_info(response[4:], expect_dtc=True)
            except ValueError as error:
                log.write(f"Invalid/incomplete Ford Type-2 KOER result: {error}")
                log.write("Stopping rather than guessing that the routine completed.")
                return False

            log_type2_info(log, info, context="KOER result")
            if info.status == "completed":
                log.write("Ford Type-2 RoutineInfo explicitly reports COMPLETED (0x20).")
                return True
            if info.status == "aborted":
                log.write("Ford Type-2 RoutineInfo reports ABORTED (0x21).")
                return False
            if info.status == "active":
                log.write(f"KOER still active; waiting {poll_interval:.1f} sec before checking again.")
                time.sleep(poll_interval)
                continue

        code = nrc_of(response)
        if code == 0x21:
            busy_count += 1
            elapsed = time.monotonic() - started
            log.write(
                f"PCM busy with KOER (busyRepeatRequest #{busy_count}, elapsed {elapsed:.0f}s). "
                f"Waiting {poll_interval:.1f} sec before ONE new result request."
            )
            if time.monotonic() - last_reminder >= 30.0:
                log.write("REMINDER: keep engine running; perform the brake/steering driver inputs once; let engine reach normal operating temperature.")
                last_reminder = time.monotonic()
            time.sleep(poll_interval)
            continue

        if code == 0x22 or code in CONDITION_NRCS:
            log.write(f"KOER result not ready/condition changed: {describe_nrc(response)}")
            time.sleep(poll_interval)
            continue
        if code in SESSION_MISMATCH_NRCS:
            log.write(
                "Diagnostic session no longer permits KOER result retrieval. "
                "Stopping; the script will not re-enter a session mid-routine."
            )
            return False
        if code == 0x33:
            log.write("SecurityAccess denied. Stopping; SecurityAccess will NOT be attempted.")
            return False

        log.write(f"Unexpected terminal KOER result response: {fmt(response)}")
        return False

    log.write("Timed out waiting for an unambiguous Ford Type-2 KOER completion result.")
    return False


def foil_wizard(log: RunLog) -> None:
    log.write("\n**************************************************")
    log.write("KOER COMPLETE")
    log.write("LEAVE ENGINE RUNNING")
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
    print("Ford Focus interactive KOER runner - DRY RUN")
    print("\nNO CAN FRAMES WILL BE TRANSMITTED.\n")
    print("Evidence-backed KOER transaction:")
    print("  primary:  10 03 -> expect 50 03 -> 31 01 02 82")
    print("  fallback: 10 01 -> expect 50 01 -> 31 01 02 82")
    print("            fallback is used ONLY after NRC 0x7E/0x7F in extended session")
    print("\nExpected Ford Type-2 RoutineInfo:")
    print("  0x22 = Type 2 / active")
    print("  0x21 = Type 2 / aborted")
    print("  0x20 = Type 2 / completed")
    print("\nAfter start is active:")
    print("  show Ford KOER driver-input guidance")
    print("  poll 31 03 02 82 only, one request per poll interval")
    print("  NRC 0x21 busyRepeatRequest during polling = PCM busy; wait and try later")
    print("  completed results must contain RoutineInfo 0x20 plus Ford's 3-byte On-Demand DTC field")
    print("  NRC 0x78 is waited out without resending start")
    print("\nKnown failed requests are NOT retransmitted:")
    print("  31 01 02 02")
    print("  31 02 00")
    print("  31 82")
    print("\nNever attempted: PATS, SecurityAccess, writes, resets, programming, flashing, arbitrary RIDs.")
    print("\nThe foil procedure is never interactive during dry-run.")
    return 0


def run(args: argparse.Namespace) -> int:
    if not args.execute:
        return dry_run()

    print("\nFORD FOCUS INTERACTIVE KOER RUNNER")
    print("==================================")
    print("START THE CAR NORMALLY AND LEAVE THE ENGINE RUNNING")
    print("PARK, PARKING BRAKE, VEHICLE STATIONARY")
    print("A/C AND ACCESSORIES OFF")
    print("DRIVER'S DOOR CLOSED")
    print("NO FOIL YET")
    print("\nFord service material specifies normal operating temperature for KOER.")
    print("This tool only invokes Ford KOER RID 0x0282. It does not scan arbitrary ECU routines.")
    confirm = input("\nType KOER to begin: ").strip().upper()
    if confirm != "KOER":
        print("Cancelled. Nothing transmitted.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log = RunLog(Path("logs") / f"koer_scan_{stamp}.log")
    candidates = [CandidateState(name, request, positive_prefix) for name, request, positive_prefix in SESSION_CANDIDATES]
    candidate_index = 0
    abort_retries = 0

    try:
        port = args.port or find_port()
        log.write(f"CANable: {port}")
        log.write(f"PCM: 0x{PCM_TX_ID:X} -> 0x{PCM_RX_ID:X}")
        log.write(f"KOER RID: 0x{KOER_RID:04X} (Ford Key-On Engine Running Self Test)")
        log.write("Start payload: 31 01 02 82")
        log.write("Results payload: 31 03 02 82")

        with Slcan(port) as bus:
            bus.open_channel(500000, silent=False)
            client = IsoTpClient(bus, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=2.0)

            cycle = 0
            while True:
                cycle += 1
                log.write(f"\n################ KOER CYCLE {cycle} ################")

                default_response = exchange_busy_retry(
                    client,
                    bytes.fromhex("10 01"),
                    log,
                    label="default session before read-only preflight",
                    timeout=10.0,
                )
                if not positive(default_response, bytes.fromhex("50 01")):
                    raise RuntimeError(
                        f"could not establish default session for preflight: {describe_nrc(default_response)}"
                    )

                values = read_preflight(client, log)
                coolant = values.get(0x05)
                runtime = values.get(0x1F)
                if coolant is not None:
                    log.write(f"NOTE: coolant={coolant:.0f} C; no invented numeric KOER threshold is enforced.")
                if runtime is not None:
                    log.write(f"NOTE: engine runtime={runtime:.0f} sec; no invented numeric minimum is enforced.")

                candidate = candidates[candidate_index]
                outcome, _start_info = try_start_candidate(client, candidate, log)

                if outcome in ("accepted", "completed"):
                    if outcome == "accepted":
                        print_operator_actions(log, coolant_c=coolant, runtime_s=runtime)
                    completed = poll_koer_results(
                        client,
                        log,
                        max_seconds=args.result_timeout,
                        poll_interval=args.poll_interval,
                    )
                    if completed:
                        log.write("KOER completion positively decoded. Closing CAN channel before manual steps.")
                        bus.shutdown()
                        foil_wizard(log)
                        return 0
                    log.write("KOER started but completion was not positively established. Stopping without foil.")
                    return 2

                if outcome == "wrong-session":
                    if candidate_index + 1 < len(candidates):
                        candidate_index += 1
                        next_candidate = candidates[candidate_index]
                        log.write(
                            f"Ford returned an active-session NRC. Switching once to explicit fallback: "
                            f"{next_candidate.name} session."
                        )
                        continue
                    log.write("KOER is unavailable in both allowlisted sessions. Stopping safely.")
                    break

                if outcome == "condition":
                    log.write(
                        f"KOER entry condition not satisfied. Keeping the same {candidate.name} session candidate; "
                        f"refreshing live values and retrying in {args.retry_delay:.1f} sec."
                    )
                elif outcome == "aborted":
                    abort_retries += 1
                    if abort_retries > args.max_abort_retries:
                        log.write(
                            f"KOER aborted more than {args.max_abort_retries} times. "
                            "Stopping instead of looping actuators indefinitely."
                        )
                        break
                    log.write(
                        f"KOER reported aborted; retry {abort_retries}/{args.max_abort_retries} "
                        f"in {args.retry_delay:.1f} sec."
                    )
                else:
                    log.write("KOER request produced a terminal/unknown response. No alternate RID or service will be tried.")
                    break

                if args.max_cycles and cycle >= args.max_cycles:
                    log.write(f"Reached max KOER cycles ({args.max_cycles}). Stopping safely.")
                    break
                time.sleep(args.retry_delay)

        log.write("\nKOER was not positively completed. DO NOT START THE FOIL PROCEDURE.")
        log.write("\nSummary:")
        for candidate in candidates:
            last = "none" if candidate.last_response is None else fmt(candidate.last_response)
            log.write(
                f"  {candidate.name}: attempts={candidate.attempts}, "
                f"session_mismatch={candidate.session_mismatch}, last={last}"
            )
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
    parser = argparse.ArgumentParser(description="Interactive, allowlisted Ford Focus PCM KOER runner")
    parser.add_argument("--execute", action="store_true", help="actually transmit the evidence-backed KOER request")
    parser.add_argument("--port", help="CANable serial port; defaults to CANABLE_PORT/autodetection")
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="maximum entry-condition retry cycles; 0 means retry until success, terminal response, or Ctrl-C",
    )
    parser.add_argument("--retry-delay", type=float, default=3.0, help="seconds between entry-condition retries")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.5,
        help="seconds between KOER result requests; live 0x21 busy replies are not hammered with immediate retries",
    )
    parser.add_argument(
        "--result-timeout",
        type=float,
        default=300.0,
        help="seconds to allow KOER to remain active while results are polled",
    )
    parser.add_argument(
        "--max-abort-retries",
        type=int,
        default=2,
        help="maximum retries after an explicit Ford Type-2 aborted status",
    )
    return parser


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
