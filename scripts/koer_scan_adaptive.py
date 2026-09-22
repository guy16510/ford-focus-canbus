#!/usr/bin/env python3
from __future__ import annotations

import sys
import time

try:
    from scripts import koer_scan as base
    from scripts import koer_scan_keepalive as keepalive
except ImportError:  # Direct execution from scripts/.
    import koer_scan as base
    import koer_scan_keepalive as keepalive


# Live 2013 Focus evidence:
# - KOER start 31 01 02 82 -> 71 01 02 82 22 (Type-2 ACTIVE)
# - result polls returned 7F 31 21 for ~93 seconds
# - then result requests became silent while 3E 00 continued to return 7E 00
#
# The silent phase is therefore NOT session loss. Once it begins, reduce service
# 0x31 traffic substantially while continuing the already-live-confirmed
# TesterPresent keepalive. Never infer completion from silence.
BUSY_RESULT_INTERVAL = 3.0
QUIET_RESULT_INTERVAL = 10.0
KEEPALIVE_INTERVAL = 2.0


def wait_with_keepalive(client, log: base.RunLog, seconds: float, enabled: bool) -> bool:
    """Wait without letting the extended session expire.

    When TesterPresent has been positively confirmed on the live PCM, split a
    longer wait into small pieces and send 3E 00 about every two seconds. If the
    keepalive ever stops being positively acknowledged, disable it rather than
    trying another subfunction or re-entering the diagnostic session.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if not enabled:
            time.sleep(max(0.0, remaining))
            break

        sleep_for = min(KEEPALIVE_INTERVAL, max(0.0, remaining))
        if sleep_for:
            time.sleep(sleep_for)
        if time.monotonic() >= deadline:
            break

        if not keepalive.tester_present(client, log):
            log.write("TesterPresent stopped responding positively during wait; explicit keepalive disabled.")
            enabled = False
    return enabled


def decode_result(response: bytes, log: base.RunLog) -> str:
    """Return completed, aborted, active, or other for one KOER result reply."""
    if not base.positive(response, base.KOER_RESULTS_POSITIVE):
        return "other"

    try:
        info = base.decode_type2_routine_info(response[4:], expect_dtc=True)
    except ValueError as error:
        log.write(f"Invalid/incomplete Ford Type-2 KOER result: {error}")
        return "invalid"

    base.log_type2_info(log, info, context="KOER result")
    return info.status


def poll_koer_results(
    client,
    log: base.RunLog,
    *,
    max_seconds: float = 300.0,
    poll_interval: float = 2.5,
) -> bool:
    """Poll KOER with an adaptive low-traffic quiet phase.

    The PCM is allowed to run the already-accepted KOER routine without being
    hammered by RoutineControl result requests. 3E 00 is used only because this
    exact PCM has positively acknowledged it as TesterPresent.
    """
    log.write("\nPolling KOER RID 0x0282 with adaptive quiet-phase handling.")
    log.write(
        "A 7F 31 21 reply means the accepted routine is busy. If result requests "
        "later go silent while 3E 00 still returns 7E 00, the diagnostic session "
        "is alive; result polling will back off to once every 10 seconds."
    )

    keepalive_enabled = keepalive.tester_present(client, log)
    if keepalive_enabled:
        log.write("TesterPresent 3E 00 -> 7E 00 confirmed. Session keepalive ENABLED.")
    else:
        log.write("TesterPresent was not positively confirmed. Continuing without explicit keepalive.")

    started = time.monotonic()
    deadline = started + max_seconds
    busy_count = 0
    silent_count = 0
    quiet_phase = False
    last_reminder = started

    while time.monotonic() < deadline:
        try:
            response = keepalive.exchange_short(
                client,
                base.KOER_RESULTS,
                log,
                label="request KOER RID 0x0282 results",
            )
        except TimeoutError:
            silent_count += 1
            elapsed = time.monotonic() - started
            if not quiet_phase:
                quiet_phase = True
                log.write(
                    "KOER result service entered a SILENT PHASE while the PCM remains responsive. "
                    "This is not treated as completion or failure. Backing result requests off to "
                    f"every {QUIET_RESULT_INTERVAL:.0f} seconds while maintaining TesterPresent."
                )
            log.write(
                f"No reply to KOER result request within {keepalive.INITIAL_RESPONSE_TIMEOUT:.1f}s "
                f"(silent #{silent_count}, elapsed {elapsed:.0f}s)."
            )
            keepalive_enabled = wait_with_keepalive(
                client,
                log,
                min(QUIET_RESULT_INTERVAL, max(0.0, deadline - time.monotonic())),
                keepalive_enabled,
            )
            continue

        state = decode_result(response, log)
        if state == "completed":
            log.write("Ford Type-2 RoutineInfo explicitly reports COMPLETED (0x20).")
            return True
        if state == "aborted":
            log.write("Ford Type-2 RoutineInfo explicitly reports ABORTED (0x21).")
            return False
        if state == "invalid":
            log.write("Stopping rather than guessing that KOER completed.")
            return False
        if state == "active":
            interval = QUIET_RESULT_INTERVAL if quiet_phase else max(BUSY_RESULT_INTERVAL, poll_interval)
            log.write(f"KOER still ACTIVE. Next result check in {interval:.1f}s.")
            keepalive_enabled = wait_with_keepalive(client, log, interval, keepalive_enabled)
            continue

        code = base.nrc_of(response)
        if code == 0x21:
            busy_count += 1
            elapsed = time.monotonic() - started
            interval = QUIET_RESULT_INTERVAL if quiet_phase else max(BUSY_RESULT_INTERVAL, poll_interval)
            log.write(
                f"PCM busy with accepted KOER (busyRepeatRequest #{busy_count}, elapsed {elapsed:.0f}s). "
                f"Next result check in {interval:.1f}s."
            )
            if time.monotonic() - last_reminder >= 30.0:
                log.write("REMINDER: engine running, vehicle stationary, NO FOIL until explicit completion.")
                last_reminder = time.monotonic()
            keepalive_enabled = wait_with_keepalive(client, log, interval, keepalive_enabled)
            continue

        if code == 0x22 or code in base.CONDITION_NRCS:
            interval = QUIET_RESULT_INTERVAL if quiet_phase else max(BUSY_RESULT_INTERVAL, poll_interval)
            log.write(f"KOER not ready/condition response: {base.describe_nrc(response)}")
            keepalive_enabled = wait_with_keepalive(client, log, interval, keepalive_enabled)
            continue

        if code in base.SESSION_MISMATCH_NRCS:
            log.write(
                "PCM says the active diagnostic session no longer permits KOER result retrieval. "
                "Stopping without foil; no mid-routine session re-entry will be attempted."
            )
            return False
        if code == 0x33:
            log.write("SecurityAccess denied. Stopping; SecurityAccess will NOT be attempted.")
            return False

        log.write(f"Unexpected terminal KOER result response: {base.fmt(response)}")
        return False

    # One final evidence-backed result request before declaring that completion
    # was not established. Silence is never treated as success.
    log.write("KOER result window reached its limit; making ONE final result request.")
    try:
        response = keepalive.exchange_short(
            client,
            base.KOER_RESULTS,
            log,
            label="final KOER RID 0x0282 result request",
        )
    except TimeoutError:
        log.write("Final result request was silent. KOER completion was NOT established.")
        return False

    state = decode_result(response, log)
    if state == "completed":
        log.write("Ford Type-2 RoutineInfo explicitly reports COMPLETED (0x20).")
        return True
    if state == "aborted":
        log.write("Ford Type-2 RoutineInfo explicitly reports ABORTED (0x21).")
    else:
        log.write("KOER completion was not explicitly established. DO NOT START THE FOIL PROCEDURE.")
    return False


def main() -> int:
    # Keep all audited entry/preflight/RID/completion/foil gating from the base
    # runner. Replace only the active-routine result polling behavior.
    base.poll_koer_results = poll_koer_results
    return base.main()


if __name__ == "__main__":
    sys.exit(main())
