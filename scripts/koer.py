#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port

PCM_TX_ID, PCM_RX_ID = 0x7E0, 0x7E8
EXTENDED_SESSION = bytes.fromhex("10 03")
KOER_START = bytes.fromhex("31 01 02 02")
KOER_RESULTS = bytes.fromhex("31 03 02 02")
TESTER_PRESENT = bytes.fromhex("3E 00")

NRC = {
    0x10: "generalReject", 0x11: "serviceNotSupported", 0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLengthOrInvalidFormat", 0x21: "busyRepeatRequest",
    0x22: "conditionsNotCorrect", 0x24: "requestSequenceError", 0x31: "requestOutOfRange",
    0x33: "securityAccessDenied", 0x78: "responsePending",
    0x7E: "subFunctionNotSupportedInActiveSession", 0x7F: "serviceNotSupportedInActiveSession",
    0x81: "rpmTooHigh", 0x82: "rpmTooLow", 0x83: "engineIsRunning",
    0x84: "engineIsNotRunning", 0x85: "engineRunTimeTooLow", 0x86: "temperatureTooHigh",
    0x87: "temperatureTooLow", 0x88: "vehicleSpeedTooHigh",
    0x8B: "transmissionRangeNotInNeutral", 0x8F: "shifterLeverNotInPark",
    0x92: "voltageTooHigh", 0x93: "voltageTooLow",
}


@dataclass(frozen=True)
class ObdDefinition:
    pid: int
    label: str
    unit: str
    width: int
    decoder: Callable[[bytes], float]


@dataclass(frozen=True)
class ObdReading:
    pid: int
    label: str
    value: float
    unit: str


@dataclass(frozen=True)
class FordDid:
    did: int
    label: str


@dataclass
class PreflightSnapshot:
    timestamp: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    readings: dict[int, ObdReading] = field(default_factory=dict)
    unsupported_pids: list[int] = field(default_factory=list)
    did_values: dict[int, str] = field(default_factory=dict)
    did_notes: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreflightDecision:
    can_attempt: bool
    blockers: tuple[str, ...]
    notes: tuple[str, ...]


OBD_DEFINITIONS = {
    0x0C: ObdDefinition(0x0C, "RPM", "rpm", 2, lambda data: int.from_bytes(data, "big") / 4),
    0x0D: ObdDefinition(0x0D, "Speed", "km/h", 1, lambda data: float(data[0])),
    0x05: ObdDefinition(0x05, "Coolant", "C", 1, lambda data: float(data[0] - 40)),
    0x1F: ObdDefinition(0x1F, "Engine runtime", "sec", 2, lambda data: float(int.from_bytes(data, "big"))),
    0x42: ObdDefinition(0x42, "Voltage", "V", 2, lambda data: int.from_bytes(data, "big") / 1000),
    0x11: ObdDefinition(0x11, "Throttle", "%", 1, lambda data: data[0] * 100 / 255),
    0x04: ObdDefinition(0x04, "Engine load", "%", 1, lambda data: data[0] * 100 / 255),
}

# The Ford-specific names have Ford-family definition evidence, but the record
# layouts are not proven for HFCR3PS.H32. Query each once and retain raw bytes.
FORD_DIDS = (
    FordDid(0xF186, "Active Diagnostic Session (ISO 14229)"),
    FordDid(0xD100, "Active Diagnostic Session (Ford candidate)"),
    FordDid(0x1126, "Time Since Start"),
    FordDid(0x1505, "Vehicle Speed - High Resolution"),
    FordDid(0x038F, "Engine Coolant Temperature - Corrected"),
    FordDid(0x054F, "Battery / terminal voltage"),
    FordDid(0x062E, "Engine-speed / tachometer-related value"),
)


def fmt(data: bytes) -> str:
    return data.hex(" ").upper()


def describe_negative(response: bytes) -> str:
    if len(response) >= 3 and response[0] == 0x7F:
        return f"service=0x{response[1]:02X} NRC=0x{response[2]:02X} ({NRC.get(response[2], 'unknown')})"
    return "not a negative response"


def request_wait_pending(client: IsoTpClient, payload: bytes, *, label: str, overall_timeout: float = 45.0) -> bytes:
    print(f"TX 0x{PCM_TX_ID:X}: {fmt(payload)}  [{label}]")
    client.send_payload(payload)
    deadline = time.monotonic() + overall_timeout
    while time.monotonic() < deadline:
        try:
            response = client.receive_payload()
        except TimeoutError:
            continue
        print(f"RX 0x{PCM_RX_ID:X}: {fmt(response)}")
        if len(response) >= 3 and response[:2] == bytes((0x7F, payload[0])) and response[2] == 0x78:
            print("  response pending, continuing to wait")
            continue
        return response
    raise TimeoutError(f"Timed out waiting for final response to {label}")


def positive(response: bytes, prefix: bytes) -> bool:
    return len(response) >= len(prefix) and response[:len(prefix)] == prefix


def render_failure(label: str, response: bytes) -> str:
    detail = describe_negative(response) if response[:1] == b"\x7f" else "status=unexpected"
    return ("\nKOER DID NOT COMPLETE. DO NOT PERFORM THE FOIL/MYKEY PROCEDURE.\n"
            f"Final {label} response: RX 0x{PCM_RX_ID:X}: {fmt(response)} ({detail})")


def parse_supported_pids(base_pid: int, bitmap_bytes: bytes) -> set[int]:
    if len(bitmap_bytes) != 4:
        raise ValueError("supported-PID bitmap must contain four bytes")
    bitmap = int.from_bytes(bitmap_bytes, "big")
    return {base_pid + offset for offset in range(1, 33) if bitmap & (1 << (32 - offset))}


def decode_obd_response(pid: int, response: bytes) -> ObdReading:
    definition = OBD_DEFINITIONS[pid]
    if response[:2] != bytes((0x41, pid)):
        raise ValueError(f"unexpected response for PID 0x{pid:02X}: {fmt(response)}")
    raw = response[2:2 + definition.width]
    if len(raw) != definition.width:
        raise ValueError(f"short response for PID 0x{pid:02X}: {fmt(response)}")
    return ObdReading(pid, definition.label, definition.decoder(raw), definition.unit)


def _read_optional(client: IsoTpClient, request: bytes, label: str) -> bytes | None:
    try:
        return request_wait_pending(client, request, label=label, overall_timeout=6.0)
    except TimeoutError as error:
        print(f"  no response ({error}); continuing")
        return None


def collect_supported_pids(client: IsoTpClient) -> set[int]:
    supported: set[int] = set()
    for base in (0x00, 0x20, 0x40):
        if base and base not in supported:
            break
        response = _read_optional(client, bytes((0x01, base)), f"OBD supported PIDs 0x{base + 1:02X}-0x{base + 0x20:02X}")
        if response is None or response[:2] != bytes((0x41, base)) or len(response) < 6:
            print(f"  supported-PID bitmap 0x{base:02X} unavailable")
            break
        supported.update(parse_supported_pids(base, response[2:6]))
    return supported


def decode_did_note(did: int, raw: bytes) -> str:
    if did != 0xF186 or len(raw) != 1:
        return "raw value; record layout for this PCM is UNKNOWN"
    return {0x01: "default", 0x02: "programming", 0x03: "extended"}.get(raw[0], f"unknown session 0x{raw[0]:02X}")


def collect_preflight(client: IsoTpClient) -> PreflightSnapshot:
    snapshot = PreflightSnapshot()
    supported = collect_supported_pids(client)
    for pid, definition in OBD_DEFINITIONS.items():
        if pid not in supported:
            snapshot.unsupported_pids.append(pid)
            continue
        response = _read_optional(client, bytes((0x01, pid)), f"OBD {definition.label}")
        if response is None:
            continue
        try:
            snapshot.readings[pid] = decode_obd_response(pid, response)
        except ValueError as error:
            print(f"  {error}; retaining UNKNOWN")
    for definition in FORD_DIDS:
        request = bytes((0x22, definition.did >> 8, definition.did & 0xFF))
        response = _read_optional(client, request, f"DID 0x{definition.did:04X} {definition.label}")
        if response is None:
            snapshot.did_notes[definition.did] = "no response"
            continue
        if len(response) >= 3 and response[:2] == b"\x7f\x22":
            snapshot.did_notes[definition.did] = f"NRC 0x{response[2]:02X} ({NRC.get(response[2], 'unknown')})"
            continue
        if response[:3] != bytes((0x62, definition.did >> 8, definition.did & 0xFF)):
            snapshot.did_notes[definition.did] = f"unexpected response {fmt(response)}"
            continue
        raw = response[3:]
        snapshot.did_values[definition.did] = fmt(raw)
        snapshot.did_notes[definition.did] = decode_did_note(definition.did, raw)
    return snapshot


def evaluate_preflight(snapshot: PreflightSnapshot) -> PreflightDecision:
    blockers: list[str] = []
    notes: list[str] = []
    rpm, speed = snapshot.readings.get(0x0C), snapshot.readings.get(0x0D)
    voltage, coolant = snapshot.readings.get(0x42), snapshot.readings.get(0x05)
    if rpm is None:
        blockers.append("engine-running state is UNKNOWN (RPM unavailable)")
    elif rpm.value <= 0:
        blockers.append("engine is not running")
    if speed is None:
        blockers.append("stationary state is UNKNOWN (vehicle speed unavailable)")
    elif speed.value != 0:
        blockers.append("vehicle speed is not zero")
    if voltage is None:
        blockers.append("module voltage is UNKNOWN")
    elif not 11.0 <= voltage.value <= 18.0:
        blockers.append("module voltage is outside Ford's documented 11-18 V diagnostic operating range")
    if coolant is not None:
        notes.append("exact Ford Focus KOER coolant threshold is UNKNOWN; Ford requires normal operating temperature")
    if snapshot.readings.get(0x1F) is not None:
        notes.append("exact Ford Focus KOER minimum engine runtime is UNKNOWN")
    notes.append("gear, brake, and accelerator state are UNKNOWN unless a proven DID reports them")
    return PreflightDecision(not blockers, tuple(blockers), tuple(notes))


def _display_value(snapshot: PreflightSnapshot, pid: int) -> str:
    reading = snapshot.readings.get(pid)
    if reading is None:
        return "UNKNOWN"
    precision = 1 if pid in (0x42, 0x11, 0x04) else 0
    return f"{reading.value:.{precision}f} {reading.unit}"


def render_preflight(snapshot: PreflightSnapshot, decision: PreflightDecision, *, output_fn: Callable[[str], None] = print) -> None:
    output_fn("\n## KOER PRECHECK")
    for pid in (0x0C, 0x0D, 0x05, 0x1F, 0x42, 0x11, 0x04):
        output_fn(f"{OBD_DEFINITIONS[pid].label + ':':17} {_display_value(snapshot, pid)}")
    output_fn(f"{'Session:':17} {snapshot.did_notes.get(0xF186, 'UNKNOWN')}")
    output_fn(f"{'Gear:':17} UNKNOWN")
    output_fn(f"{'Brake:':17} UNKNOWN")
    output_fn("Ford DID observations:")
    for definition in FORD_DIDS:
        raw = snapshot.did_values.get(definition.did, "UNKNOWN")
        note = snapshot.did_notes.get(definition.did, "not returned")
        output_fn(f"  0x{definition.did:04X} {definition.label}: {raw} ({note})")
    output_fn("Possible blocker:")
    if decision.blockers:
        for blocker in decision.blockers:
            output_fn(f"  {blocker}")
    else:
        output_fn("  no definite blocker found in readable data")
    for note in decision.notes:
        output_fn(f"  NOTE: {note}")


def snapshot_dict(snapshot: PreflightSnapshot) -> dict[str, Any]:
    return {
        "timestamp": snapshot.timestamp,
        "obd": {f"{pid:02X}": {"label": value.label, "value": value.value, "unit": value.unit}
                for pid, value in sorted(snapshot.readings.items())},
        "unsupported_pids": [f"{pid:02X}" for pid in snapshot.unsupported_pids],
        "ford_dids": {f"{did:04X}": {"raw": raw, "note": snapshot.did_notes.get(did, "")}
                      for did, raw in sorted(snapshot.did_values.items())},
        "did_notes": {f"{did:04X}": note for did, note in sorted(snapshot.did_notes.items())},
    }


def snapshot_from_dict(document: dict[str, Any]) -> PreflightSnapshot:
    data = document.get("snapshot", document)
    readings = {int(pid, 16): ObdReading(int(pid, 16), item["label"], float(item["value"]), item["unit"])
                for pid, item in data.get("obd", {}).items()}
    return PreflightSnapshot(timestamp=data.get("timestamp", "unknown"), readings=readings)


def diff_snapshots(previous: PreflightSnapshot, current: PreflightSnapshot) -> list[str]:
    changes: list[str] = []
    for pid in sorted(set(previous.readings) | set(current.readings)):
        before, after = previous.readings.get(pid), current.readings.get(pid)
        if before == after:
            continue
        label = (after or before).label
        old = "UNKNOWN" if before is None else f"{before.value:g} {before.unit}"
        new = "UNKNOWN" if after is None else f"{after.value:g} {after.unit}"
        changes.append(f"{label}: {old} -> {new}")
    return changes


def latest_failed_preflight(log_dir: Path) -> PreflightSnapshot | None:
    candidates = sorted(log_dir.glob("koer_preflight_*.json"))
    if not candidates:
        return None
    try:
        return snapshot_from_dict(json.loads(candidates[-1].read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_failed_preflight(snapshot: PreflightSnapshot, log_dir: Path, response: bytes) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    path = log_dir / f"koer_preflight_{stamp}.json"
    path.write_text(json.dumps({"response": fmt(response), "snapshot": snapshot_dict(snapshot)}, indent=2, sort_keys=True) + "\n")
    return path


def manual_wizard(*, input_fn: Callable[[str], str] = input, output_fn: Callable[[str], None] = print) -> bool:
    output_fn("\n==================================================\nKOER COMPLETE - LEAVE THE ENGINE RUNNING\n==================================================")
    output_fn("DO NOT TURN THE KEY OFF YET.\n")
    output_fn("Now get several layers of aluminum foil ready.\nWrap the PLASTIC HEAD of the ignition key tightly in foil.\nCover the transponder area completely.\nLeave the METAL BLADE exposed so the key can still turn.\n\nThe engine must STILL BE RUNNING while you wrap the key.\nDriver's door MUST remain CLOSED.")
    input_fn("Press ENTER ONLY AFTER the key head is wrapped, the engine is still running, and the driver's door is still closed: ")
    output_fn("\n==================================================\nSTEP 2 - TURN ENGINE OFF\n==================================================\nTurn the ignition fully to OFF now.\nDO NOT open the driver's door, remove the foil, or remove the key unless absolutely required.\nDriver's door MUST remain CLOSED.")
    input_fn("Press ENTER as soon as the ignition is fully OFF: ")
    output_fn("\n==================================================\nSTEP 3 - TURN KEY BACK TO RUN/ON\n==================================================\nImmediately turn the key forward to RUN/ON.\nIMPORTANT: DO NOT CRANK THE ENGINE. DO NOT START THE ENGINE.\nLeave the foil on the key. Driver's door MUST remain CLOSED.")
    input_fn("Press ENTER once the ignition is in RUN/ON and the engine is NOT running: ")
    output_fn("\n==================================================\nSTEP 4 - CLEAR MYKEY\n==================================================\nOn the instrument cluster:\nSettings\n-> MyKey\n-> Clear MyKeys / Clear All MyKeys\n\nSelect it and HOLD OK until the cluster confirms that all MyKeys have been cleared.\nDo not remove the foil yet.")
    confirmation = input_fn("Press ENTER after the cluster explicitly confirms the MyKeys were cleared (type NO if it did not): ").strip().lower()
    if confirmation == "no":
        output_fn("\nThe cluster did not confirm that MyKeys were cleared. The attempt did not succeed.\nSave the exact terminal log; do not try random diagnostic commands.")
        return False
    output_fn("\n==================================================\nSTEP 5 - FINISH\n==================================================\nRemove the foil from the key.\nTurn the ignition OFF.\nWait a few seconds.\nStart the vehicle normally.\nVerify the MyKey warning/count and restrictions are gone, and the key behaves as an unrestricted/admin key.")
    input_fn("Press ENTER when finished: ")
    output_fn("\nProcedure complete.\nIf the cluster never offered Clear MyKeys, the attempt did not succeed. Save the exact terminal log; do not try random diagnostic commands.")
    return True


def render_dry_run_manual_steps(*, output_fn: Callable[[str], None] = print) -> None:
    output_fn("\nSIMULATION ONLY - manual steps that would be shown after confirmed KOER completion:")
    output_fn("  1. Simulated: cover the plastic key head with foil while the engine remains running.")
    output_fn("  2. Simulated: ignition OFF, then promptly RUN/ON without cranking.")
    output_fn("  3. Simulated: use Settings -> MyKey -> Clear All and wait for confirmation.")
    output_fn("No physical action should be taken during a dry run.")


def run_koer(client: IsoTpClient, snapshot: PreflightSnapshot, max_seconds: float, log_dir: Path) -> None:
    input("\nPreflight looks plausible. Press ENTER to retry exact requests 10 03 and 31 01 02 02 (Ctrl-C cancels): ")
    session = request_wait_pending(client, EXTENDED_SESSION, label="extended diagnostic session", overall_timeout=10.0)
    if not positive(session, bytes.fromhex("50 03")):
        raise SystemExit(render_failure("extended-session", session))
    start = request_wait_pending(client, KOER_START, label="start On-Demand Self-Test RID 0x0202", overall_timeout=45.0)
    if not positive(start, bytes.fromhex("71 01 02 02")):
        if start == bytes.fromhex("7F 31 22"):
            previous = latest_failed_preflight(log_dir)
            print("\nPreflight snapshot at conditionsNotCorrect failure:")
            render_preflight(snapshot, evaluate_preflight(snapshot))
            changes = [] if previous is None else diff_snapshots(previous, snapshot)
            print("Changed since previous failed attempt:")
            print("  no prior failed snapshot" if previous is None else ("\n".join(f"  {item}" for item in changes) or "  none"))
            print(f"Saved failure snapshot: {save_failed_preflight(snapshot, log_dir, start)}")
        raise SystemExit(render_failure("KOER-start", start))
    print("\nKOER start accepted. Monitoring results...")
    deadline, last_tester_present = time.monotonic() + max_seconds, 0.0
    while time.monotonic() < deadline:
        if time.monotonic() - last_tester_present >= 2.0:
            tp = request_wait_pending(client, TESTER_PRESENT, label="TesterPresent", overall_timeout=8.0)
            if not positive(tp, bytes.fromhex("7E 00")):
                raise SystemExit(render_failure("TesterPresent", tp))
            last_tester_present = time.monotonic()
        result = request_wait_pending(client, KOER_RESULTS, label="request KOER results", overall_timeout=20.0)
        if not positive(result, bytes.fromhex("71 03 02 02")) or len(result) < 5:
            raise SystemExit(render_failure("KOER-results", result))
        status = result[4] & 0x0F
        print(f"  routineInfo=0x{result[4]:02X}, status={status}")
        if status == 0:
            print("\nKOER completed. Stopping diagnostic traffic before the manual procedure.")
            return
        if status == 1:
            raise SystemExit(render_failure("KOER-results", result) + "\nInterpreted status: ABORTED")
        if status != 2:
            raise SystemExit(render_failure("KOER-results", result) + f"\nInterpreted status: UNKNOWN (0x{status:X})")
        time.sleep(1.0)
    raise SystemExit("\nKOER DID NOT COMPLETE. DO NOT PERFORM THE FOIL/MYKEY PROCEDURE.\nTimed out waiting for final result.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Guarded PCM KOER runner with read-only live preflight")
    parser.add_argument("--execute", action="store_true", help="run live preflight and offer the exact KOER retry")
    parser.add_argument("--dry-run", action="store_true", help="preview without CAN traffic or physical instructions")
    parser.add_argument("--max-seconds", type=float, default=180.0, help="maximum KOER result polling time")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"), help="failed-preflight log directory")
    args = parser.parse_args()
    if args.execute and args.dry_run:
        parser.error("--execute and --dry-run cannot be used together")
    print("Ford PCM KOER runner")
    print(f"PCM addressing: 0x{PCM_TX_ID:X} -> 0x{PCM_RX_ID:X}")
    print("Read-only preflight first; then only 10 03 and the same 31 01 02 02 request.")
    print("No alternate RID/session, PATS, SecurityAccess, writes, resets, flashing, As-Built, or programming.")
    if not args.execute:
        print("\nDRY RUN ONLY - NOTHING WILL BE TRANSMITTED.")
        render_dry_run_manual_steps()
        return
    print("\nMove the car outside, connect CANable, start the engine, keep it stationary, select Park/Neutral, set the parking brake, and keep the driver's door closed.")
    input("Press ENTER to collect the read-only live preflight (Ctrl-C cancels): ")
    port = find_port()
    print(f"\nCANable: {port}")
    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=5.5)
        snapshot = collect_preflight(client)
        decision = evaluate_preflight(snapshot)
        render_preflight(snapshot, decision)
        if not decision.can_attempt:
            raise SystemExit("\nKOER retry blocked by preflight. No routine request was sent.")
        run_koer(client, snapshot, args.max_seconds, args.log_dir)
        can.close_channel()
    manual_wizard()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSTOPPED by Ctrl-C. CAN traffic was closed; no foil/ignition procedure should be performed.")
