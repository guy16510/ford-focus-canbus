# KOER research notebook

## Objective

Reproduce the ordinary Ford PCM **Key On Engine Running On Demand Self Test (KOER)** state used by the FORScan no-admin-key MyKey workaround on this owner's 2013 Ford Focus gasoline vehicle.

This project does **not** perform PATS programming, SecurityAccess, ECU reset, flashing, As-Built/configuration writes, key programming, or arbitrary UDS routine enumeration.

## Verified live vehicle facts

```text
Vehicle:           2013 Ford Focus gasoline, blade key
PCM calibration:   HFCR3PS.H32
ECU name:          ECM.-EngineControl
PCM request ID:    0x7E0
PCM response ID:   0x7E8
Transport:         UDS / ISO-TP over HS-CAN 500 kbit/s
CAN adapter:       MKS CANable V2.0 Pro
```

Both normal UDS sessions have been accepted by the live PCM:

```text
10 01 -> 50 01 00 32 01 F4
10 03 -> 50 03 00 32 01 F4
```

The timing bytes decode to:

```text
P2ServerMax   = 50 ms
P2*ServerMax  = 5000 ms
```

## Live requests already disproved

These requests have already been tested and must not be retried as alternate KOER encodings:

```text
10 03 -> 50 03 ...
31 01 02 02 -> 7F 31 22

10 03 -> 50 03 ...
31 01 02 02 -> 7F 31 7E

10 01 -> 50 01 ...
31 01 02 02 -> 7F 31 7E

10 01 -> 50 01 ...
31 02 00 -> 7F 31 13

31 82 -> 7F 31 13
```

`31 02 00` and `31 82` are therefore the wrong message shape for this PCM. `0x0202` is a Ford generic **On-Demand Self-Test** routine identifier in the FDRS metadata; it is not the specific KOER routine we need.

## Ford FDRS/ETIS evidence for KOER RID 0x0282

A public archive of Ford ETIS Runtime/FDRS `.mdx` diagnostic definitions contains the routine mapping repeatedly across Ford diagnostic files:

```text
0202 = On-Demand Self-Test
0282 = Key-On Engine Running Self Test
```

One extracted file identified as a **Powertrain Control Module** definition (`G2059415.mdx`) contains `0x0282 = Key-On Engine Running Self Test`. The same mapping appears in numerous additional Ford diagnostic definitions.

Source archive:

- https://github.com/ghostdev137/ford-pscm-re/tree/main/firmware/_fdrs_archive
- https://github.com/ghostdev137/ford-pscm-re/blob/main/firmware/_fdrs_archive/extracted/G2059415.mdx.json
- https://github.com/ghostdev137/ford-pscm-re/blob/main/firmware/_fdrs_archive/extracted/G2049803.mdx.json

The raw string block for routine `0x0282` contains response definitions for:

```text
RoutineType
RoutineStatus
On-Demand DTCs
Key-On Engine Running Self Test
```

and start/stop/result response structures. Crucially, unlike routines that take request option records, the `0x0282` block has no `routine_routine_0282_start_req...` request-data fields before its response definitions. This is strong evidence that the start request carries no option bytes after the RID.

Therefore the evidence-backed UDS payloads are:

```text
31 01 02 82    startRoutine(0x0282)
31 03 02 82    requestRoutineResults(0x0282)
```

Positive response prefixes are:

```text
71 01 02 82 ...
71 03 02 82 ...
```

No other RID is allowlisted.

## Ford Type-2 RoutineInfo layout

Ford diagnostic metadata describes these on-demand self tests as **Type 2** routines. The one-byte `RoutineInfo` field is:

```text
bits 7..4  RoutineType
            0x2 = Type 2

bits 3..0  RoutineStatus
            0x0 = completed
            0x1 = aborted
            0x2 = active
```

So the only status bytes accepted by the runner are:

```text
20 = Type 2 / completed
21 = Type 2 / aborted
22 = Type 2 / active
```

For `requestRoutineResults`, Ford metadata additionally defines a 3-byte **On-Demand DTCs** response field. The runner therefore does not declare success from a bare positive response. A completed result must contain at least:

```text
71 03 02 82 20 XX XX XX
```

The 3-byte DTC value is logged raw; it is not guessed/decoded by this project.

A Ford diagnostic-specification dump independently exposes the same Type-2 layout and environmental NRC vocabulary:

- https://www.macheforum.com/site/threads/french-orders-exchanges-on-the-evolution-of-this-one.743/page-118

## Session behavior

Ford Type-2 On-Demand Self-Test material explicitly uses the **extended diagnostic session** for generic Type-2 self tests. The exact object-graph session reference for `0x0282` in calibration `HFCR3PS.H32` has not been fully deserialized from the proprietary MDX binary.

The safe policy is therefore:

1. use `10 03` extended session as the primary KOER session;
2. if and only if the live PCM returns `0x7E` or `0x7F` (active-session mismatch) for the `0x0282` request, make one bounded attempt in the already-live-verified `10 01` default session;
3. do not change sessions merely because of `0x22` or another environmental-condition NRC;
4. never try programming session `0x02` or any other session.

## Ford environmental NRC mapping

The Ford definitions use the following environmental negative-response codes:

```text
81 rpmTooHigh
82 rpmTooLow
83 engineIsRunning
84 engineIsNotRunning
85 engineRunTimeTooLow
86 temperatureTooHigh
87 temperatureTooLow
88 vehicleSpeedTooHigh
89 vehicleSpeedTooLow
8A throttle/PedalTooHigh
8B throttle/PedalTooLow
8C transmissionRangeNotInNeutral
8D transmissionRangeNotInGear
8F brakeSwitch(es)NotClosed
90 shifterLeverNotInPark
91 torqueConverterClutchLocked
92 voltageTooHigh
93 voltageTooLow
```

Earlier scanner versions incorrectly mapped some values around `0x8B-0x90`; that has been corrected.

`0x22 conditionsNotCorrect` remains generic. When it occurs, the runner refreshes read-only vehicle state and retries the same evidence-backed request instead of inventing a new routine/session.

## Read-only preflight

Before every KOER entry attempt, the runner reads standard SAE J1979 Mode 01 values:

```text
01 0C  engine RPM
01 0D  vehicle speed
01 05  coolant temperature
01 1F  engine run time
01 42  module voltage
```

It hard-stops if the engine is confirmed stopped or vehicle speed is nonzero. Coolant, run time and voltage are reported, but no Focus-specific threshold is invented when Ford has not supplied one for this calibration.

Vehicle preparation shown to the user:

```text
engine running normally
vehicle stationary
Park
parking brake set
A/C and accessories off
driver door closed
NO FOIL
```

## Response-pending and polling behavior

The live PCM advertises `P2*=5000 ms`. NRC `7F 31 78` therefore means the client waits for the final response without retransmitting the start request. The implementation uses the 5-second server value plus a small host/transport margin and retains an overall timeout.

After a positive Type-2 active start (`... 22`), the runner polls only:

```text
31 03 02 82
```

It does not send another start request while the routine is active. Polling diagnostic traffic also keeps the diagnostic conversation active; a separate guessed TesterPresent loop is not added.

FORScan user logs show that PCM KOER can take around 1-2 minutes on some vehicles, so the runner defaults to a 180-second result window rather than the earlier short timeout.

FORScan example log showing a KOER running for about 101 seconds:

- https://forum.forscan.org/viewtopic.php?p=86677

## FORScan MyKey workflow

FORScan's published no-admin-key workaround for gasoline vehicles is:

1. Start the car and connect.
2. Start `PCM Key On Engine Running On Demand Self Test`.
3. Wait until the test is completed.
4. For a blade key, block the transponder (for example with foil).
5. Turn ignition OFF.
6. Turn ignition back ON/RUN without starting the engine.
7. Use the instrument cluster MyKey menu to clear all MyKeys.

Source:

- https://forum.forscan.org/viewtopic.php?t=11739

The project intentionally stops CAN transmissions and closes the CAN channel before it enters the manual foil/ignition instructions.

## Current executable transaction

`scripts/koer_scan.py` implements only the bounded, evidence-backed path:

```text
read-only preflight
        |
        v
10 03
        |
        +-- accepted --> 31 01 02 82
        |                    |
        |                    +-- Type2 active 0x22 --> poll 31 03 02 82
        |                    |                              |
        |                    |                              +-- 0x22 active: continue
        |                    |                              +-- 0x21 aborted: stop/retry bounded
        |                    |                              +-- 0x20 + 3 DTC bytes: COMPLETE
        |                    |
        |                    +-- 7F/7E session mismatch --> one 10 01 fallback
        |                    +-- environmental NRC --> same-session retry
        |                    +-- unknown/unsafe NRC --> stop
        |
        +-- session failure --> stop
```

It never treats an empty result or repeated positive response as completion.

## Safety invariants

The KOER runner must never send or attempt:

```text
0x11 ECUReset
0x27 SecurityAccess
0x2E WriteDataByIdentifier
0x34 RequestDownload
0x35 RequestUpload
0x36 TransferData
0x37 RequestTransferExit
0x3D WriteMemoryByAddress
PATS/key programming
As-Built/configuration writes
arbitrary RoutineIdentifier scanning
arbitrary service scanning
programming session 0x02
```

The VIN is never committed or logged into tracked repository files.

## Remaining uncertainty

The remaining uncertainty is narrow: the Ford proprietary MDX object graph that directly ties routine `0x0282` to an allowed session has not been fully deserialized for this exact PCM calibration. The wire protocol, KOER RID, UDS request shape, Ford Type-2 result semantics, and live PCM sessions are otherwise now independently supported.

Because of that one gap, the runner uses extended session first and permits only a single default-session fallback after an explicit active-session NRC. It does not expand the search space automatically.
