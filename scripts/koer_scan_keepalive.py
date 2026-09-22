#!/usr/bin/env python3
from __future__ import annotations

import sys
import time

try:
    from scripts import koer_scan as base
except ImportError:  # Direct execution from scripts/.
    import koer_scan as base


TESTER_PRESENT = bytes.fromhex("3E 00")
TESTER_PRESENT_POSITIVE = bytes.fromhex("7E 00")

# Live 2013 Focus evidence showed the extended diagnostic session was lost after
# a KOER result request received no response and the old poller waited ~30 s.
# The PCM had previously accepted KOER and reported Type-2 ACTIVE (0x22).
#
# Keep the initial wait for a normal result response short. Only NRC 0x78 is
# allowed to extend the wait to P2* semantics. When this PCM positively supports
# standard UDS TesterPresent 3E 00, use it between result polls to keep the
# extended session alive. If TesterPresent is rejected, disable it rather than
# trying alternate subfunctions.
INITIAL_RESPONSE_TIMEOUT = 1.0
P2_STAR_TIMEOUT = 6.0
PENDING_OVERALL_TIMEOUT = 30.0
TESTER_PRESENT_INTERVAL = 2.0


def _log_rx(log: base.RunLog, response: bytes) -> None:
    log.write(f"RX 0x{base.PCM_RX_ID:X}: {base.fmt(response)}")


def receive_response(
    client,
    request_service: int,
    log: base.RunLog,
    *,
    initial_timeout: float = INITIAL_RESPONSE_TIMEOUT,
    p2_star_timeout: float = P2_STAR_TIMEOUT,
    pending_overall_timeout: float = PENDING_OVERALL_TIMEOUT,
) -> bytes:
    """Receive one UDS response without allowing a silent 30-second session gap.

    A normal request gets only the short initial-response window. If the ECU
    returns 7F <service> 78, the request is known to be pending and the client
    then honors the live PCM's 5-second P2* value plus a small host margin.
    """
    old_timeout = client.timeout
    try:
        client.timeout = initial_timeout
        response = client.receive_payload()
        _log_rx(log, response)

        if not (
            len(response) >= 3
            and response[0] == 0x7F
            and response[1] == request_service
            and response[2] == 0x78
        ):
            return response

        log.write(
            f"  NRC 0x78 responsePending; honoring P2* up to {p2_star_timeout:.1f}s per wait"
        )
        overall_deadline = time.monotonic() + pending_overall_timeout
        while time.monotonic() < overall_deadline:
            remaining = overall_deadline - time.monotonic()
            client.timeout = min(p2_star_timeout, max(0.05, remaining))
            response = client.receive_payload()
            _log_rx(log, response)
            if (
                len(response) >= 3
                and response[0] == 0x7F
                and response[1] == request_service
                and response[2] == 0x78
            ):
                log.write("  NRC 0x78 responsePending again; continuing bounded P2* wait")
                continue
            return response

        raise TimeoutError("Timed out waiting for final ECU response after NRC 0x78")
    finally:
        client.timeout = old_timeout


def exchange_short(client, payload: bytes, log: base.RunLog, *, label: str) -> bytes:
    log.write(f"TX 0x{base.PCM_TX_ID:X}: {base.fmt(payload)}  [{label}]")
    client.send_payload(payload)
    return receive_response(client, payload[0], log)


def tester_present(client, log: base.RunLog) -> bool:
    """Send standard UDS TesterPresent once and require an explicit positive reply.

    This is deliberately a capability check, not a guess. If the live PCM does
    not return 7E 00, keepalive is disabled for the run. No alternate TesterPresent
    subfunction is tried.
    """
    try:
        response = exchange_short(
            client,
            TESTER_PRESENT,
            log,
            label="TesterPresent keepalive / session capability check",
        )
    except TimeoutError:
        log.write("TesterPresent received no response; disabling explicit keepalive.")
        return False

    if response == TESTER_PRESENT_POSITIVE:
        return True

    if len(response) >= 3 and response[0] == 0x7F and response[1] == 0x3E:
        log.write(
            f"TesterPresent rejected: {base.describe_nrc(response)}; explicit keepalive disabled."
        )
        return False

    log.write(
        f"Unexpected TesterPresent response {base.fmt(response)}; explicit keepalive disabled."
    )
    return False


def poll_koer_results(
    client,
    log: base.RunLog,
    *,
    max_seconds: float = 300.0,
    poll_interval: float = 2.5,
) -> bool:
    """Poll KOER while preventing the silent session-expiry gap seen live."""
    log.write("\nPolling the SAME KOER routine for Ford Type-2 results.")
    log.write(
        "Live fix: normal result requests use a short initial-response timeout; "
        "only NRC 0x78 opens the longer P2* wait."
    )

    keepalive_enabled = tester_present(client, log)
    if keepalive_enabled:
        log.write("TesterPresent 3E 00 -> 7E 00 confirmed. Session keepalive ENABLED.")
    else:
        log.write(
            "TesterPresent was not positively confirmed. Continuing without it, "
            "but the poller will not sit silent for 30 seconds again."
        )

    deadline = time.monotonic() + max_seconds
    started = time.monotonic()
    last_keepalive = time.monotonic()
    last_reminder = started
    busy_count = 0
    silent_timeouts = 0

    while time.monotonic() < deadline:
        now = time.monotonic()
        if keepalive_enabled and now - last_keepalive >= TESTER_PRESENT_INTERVAL:
            if tester_present(client, log):
                last_keepalive = time.monotonic()
            else:
                keepalive_enabled = False

        try:
            response = exchange_short(
                client,
                base.KOER_RESULTS,
                log,
                label="request KOER RID 0x0282 results",
            )
        except TimeoutError:
            silent_timeouts += 1
            elapsed = time.monotonic() - started
            log.write(
                f"No PCM reply to KOER result request within {INITIAL_RESPONSE_TIMEOUT:.1f}s "
                f"(silent timeout #{silent_timeouts}, elapsed {elapsed:.0f}s)."
            )
            if keepalive_enabled:
                if tester_present(client, log):
                    last_keepalive = time.monotonic()
                else:
                    keepalive_enabled = False
            # Keep the gap bounded; the previous implementation waited ~30 s here
            # and the ECU subsequently reported 7F 31 7E (session lost).
            time.sleep(min(1.0, max(0.0, poll_interval)))
            continue

        if base.positive(response, base.KOER_RESULTS_POSITIVE):
            try:
                info = base.decode_type2_routine_info(response[4:], expect_dtc=True)
            except ValueError as error:
                log.write(f"Invalid/incomplete Ford Type-2 KOER result: {error}")
                log.write("Stopping rather than guessing that the routine completed.")
                return False

            base.log_type2_info(log, info, context="KOER result")
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

        code = base.nrc_of(response)
        if code == 0x21:
            busy_count += 1
            elapsed = time.monotonic() - started
            log.write(
                f"PCM busy with KOER (busyRepeatRequest #{busy_count}, elapsed {elapsed:.0f}s). "
                f"Waiting {poll_interval:.1f} sec before the next result request."
            )
            if time.monotonic() - last_reminder >= 30.0:
                log.write(
                    "REMINDER: keep the engine running and vehicle stationary. "
                    "Do not wrap the key until explicit KOER completion."
                )
                last_reminder = time.monotonic()
            time.sleep(poll_interval)
            continue

        if code == 0x22 or code in base.CONDITION_NRCS:
            log.write(f"KOER result not ready/condition changed: {base.describe_nrc(response)}")
            time.sleep(poll_interval)
            continue

        if code in base.SESSION_MISMATCH_NRCS:
            log.write(
                "Diagnostic session no longer permits KOER result retrieval. "
                "Stopping without foil; the script will not re-enter a session mid-routine."
            )
            return False

        if code == 0x33:
            log.write("SecurityAccess denied. Stopping; SecurityAccess will NOT be attempted.")
            return False

        log.write(f"Unexpected terminal KOER result response: {base.fmt(response)}")
        return False

    log.write("Timed out waiting for an unambiguous Ford Type-2 KOER completion result.")
    return False


def main() -> int:
    # Patch only the result-polling behavior. Entry, RID allowlisting, completion
    # decoding, CAN shutdown, and the manual foil gate remain in the audited base
    # runner.
    base.poll_koer_results = poll_koer_results
    return base.main()


if __name__ == "__main__":
    sys.exit(main())
