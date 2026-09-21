# AGENTS.md

## Mission

This repository is a small, auditable macOS toolchain for the owner of a **2013 Ford Focus gasoline vehicle with a blade key** and an **MKS CANable V2.0 Pro**.

The only active-control goal is to reproduce the ordinary PCM **Key On Engine Running On Demand Self Test (KOER)** state used by FORScan's no-admin-key MyKey-clearing workaround.

Do **not** implement or attempt PATS/key programming, immobilizer bypass, SecurityAccess, ECU reset, firmware flashing, arbitrary configuration/As-Built writes, memory writes, programming/download services, or arbitrary service/RoutineIdentifier brute force.

Read `docs/KOER_RESEARCH.md` before changing any KOER protocol bytes.

## Verified live vehicle/hardware facts

```text
Vehicle:           2013 Ford Focus gasoline, blade key
Host:              macOS
CAN adapter:       MKS CANable V2.0 Pro
CANable firmware:  16e7497-dirty
Observed port:     /dev/cu.usbmodem207E38A238461 (not stable; autodetect)
HS-CAN:            500000 bit/s
PCM TX/RX:         0x7E0 -> 0x7E8
PCM calibration:   HFCR3PS.H32
ECU name:          ECM.-EngineControl
Protocol:          UDS / ISO-TP
```

Wiring:

```text
OBD-II pin 6  -> CANable CANH
OBD-II pin 14 -> CANable CANL
OBD-II pin 5  -> CANable GND
CANable powered over USB
DO NOT connect OBD-II pin 16 (+12 V)
Leave CANable 120R termination disabled on the vehicle bus
```

The serial device name can change, so scripts must honor `CANABLE_PORT` and/or auto-detect.

## Verified live UDS session behavior

```text
10 01 -> 50 01 00 32 01 F4
10 03 -> 50 03 00 32 01 F4
```

Timing:

```text
P2ServerMax   = 50 ms
P2*ServerMax  = 5000 ms
```

## Live requests already disproved

Do not retry these as alternate KOER forms:

```text
31 01 02 02  -> 7F 31 22 / 7F 31 7E depending on attempt/session
31 02 00     -> 7F 31 13
31 82        -> 7F 31 13
```

The old Ford/KWP/local-routine examples are historical clues only; this PCM is using UDS-style RoutineControl.

## Current Ford FDRS evidence

Public Ford ETIS Runtime/FDRS diagnostic metadata repeatedly maps:

```text
0202 = On-Demand Self-Test
0282 = Key-On Engine Running Self Test
```

The Ford `0x0282` definition is a **Type-2** routine with one-byte `RoutineInfo`:

```text
high nibble = RoutineType
0x2 = Type 2

low nibble = RoutineStatus
0x0 = completed
0x1 = aborted
0x2 = active
```

Therefore valid routine-info bytes are:

```text
20 = Type 2 / completed
21 = Type 2 / aborted
22 = Type 2 / active
```

Ford metadata also defines a 3-byte `On-Demand DTCs` field in the result response.

The evidence-backed KOER payloads are:

```text
31 01 02 82    startRoutine(0x0282)
31 03 02 82    requestRoutineResults(0x0282)
```

Positive prefixes:

```text
71 01 02 82 ...
71 03 02 82 ...
```

Do not add request-option bytes unless new Ford evidence explicitly requires them.

## Session policy

Use extended diagnostic session first:

```text
10 03
```

Ford Type-2 self-test material uses extended session and the live PCM accepts it.

The exact proprietary MDX object-graph link between `0x0282` and an allowed session has not been fully deserialized for calibration `HFCR3PS.H32`, so retain **one bounded fallback** to already-verified default session `10 01` only if the live routine request returns:

```text
7F 31 7E
or
7F 31 7F
```

Do not change sessions for `0x22` or another environmental-condition NRC. Never try programming session `0x02`.

## Ford environmental NRCs

Keep this mapping exact:

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

Do not regress to the earlier incorrect `0x8B/0x8F` mapping.

## Read-only preflight

Before every active KOER attempt, read standard Mode 01 values where supported:

```text
01 0C RPM
01 0D vehicle speed
01 05 coolant temperature
01 1F engine runtime
01 42 module voltage
```

Stop if RPM proves the engine is not running or speed is nonzero.

Do not invent an exact Focus-specific coolant/runtime threshold. Report the values. If Ford returns a specific environmental NRC, print it and let that be authoritative.

Physical setup for KOER:

```text
engine running normally
stationary
Park
parking brake set
A/C and unnecessary accessories off
driver door closed
NO FOIL
adequate outdoor/garage ventilation
```

## KOER completion rules

A positive `71 01 02 82` is not enough by itself.

A start response must contain valid Ford Type-2 RoutineInfo. `0x22` means the routine is active.

While active, request only:

```text
31 03 02 82
```

Treat only an explicit Type-2 completed result with Ford's required result record as completion, e.g. at minimum:

```text
71 03 02 82 20 XX XX XX
```

Do not treat any of these as completion:

```text
bare 71 03 02 82
repeated positive responses with no RoutineInfo
unknown RoutineType/status
malformed or truncated completed result
```

`0x21` means aborted. `0x22` means still active.

NRC `7F 31 78` means wait for the final response without resending the start. The live PCM P2* is 5 seconds; allow a small host/transport margin.

The result window should be long enough for real KOER behavior. FORScan logs on Ford vehicles show KOER can take around 1-2 minutes; the current runner defaults to 180 seconds.

## TesterPresent

Do not add a separate guessed TesterPresent loop while `31 03 02 82` is being polled at short intervals. The result requests themselves maintain diagnostic traffic. Add explicit TesterPresent only if same-generation Ford evidence proves it is required for this transaction.

## Active script

Use:

```bash
python scripts/koer_scan.py --execute
```

The script must remain dry-run by default.

It should:

1. verify/read vehicle state;
2. enter extended session;
3. start only RID `0x0282`;
4. retry the same request on environmental conditions without inventing commands;
5. use one default-session fallback only after an explicit session-mismatch NRC;
6. poll only RID `0x0282` results;
7. require unambiguous Ford Type-2 completed status;
8. close active CAN before the manual MyKey steps.

## Manual MyKey procedure after confirmed KOER completion

Only after software has positively decoded KOER completion and stopped CAN transmissions:

1. Keep the driver's door closed.
2. Leave engine running while wrapping the **plastic head** of the blade key tightly in foil; leave metal blade exposed.
3. Turn ignition fully OFF.
4. Immediately turn back to RUN/ON **without cranking**.
5. `No key detected` is expected because the transponder is blocked.
6. Cluster: `Settings -> MyKey -> Clear MyKeys / Clear All MyKeys`.
7. Hold OK until the cluster explicitly confirms the clear.
8. Remove foil, cycle ignition, restart normally, verify restrictions/count.

Never automate ignition, immobilizer/key programming, or IPC configuration as part of this workaround.

## Forbidden active services/actions

Never add automatic use of:

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
As-Built/config writes
arbitrary RoutineIdentifier enumeration
arbitrary service enumeration
programming session 0x02
```

## Repository hygiene

- Never commit the VIN.
- Log raw diagnostic traffic but avoid unnecessary unique identifiers.
- Keep reusable transport code under `focus_can/` and workflows under `scripts/`.
- Python 3.11+.
- Keep code transparent and test protocol parsing with byte strings/mocks.
- Run `python -m unittest discover -s tests -v` before claiming a change is tested on a development machine.

## Research sources

Primary public evidence currently used:

- Ford FDRS/ETIS diagnostic archive: https://github.com/ghostdev137/ford-pscm-re/tree/main/firmware/_fdrs_archive
- PCM definition containing `0x0282`: https://github.com/ghostdev137/ford-pscm-re/blob/main/firmware/_fdrs_archive/extracted/G2059415.mdx.json
- FORScan MyKey workaround: https://forum.forscan.org/viewtopic.php?t=11739
- FORScan KOER example log: https://forum.forscan.org/viewtopic.php?p=86677
- Ford diagnostic-spec Type-2 metadata mirror: https://www.macheforum.com/site/threads/french-orders-exchanges-on-the-evolution-of-this-one.743/page-118

If future evidence conflicts with this file, preserve the live vehicle transcripts and raw Ford metadata, update `docs/KOER_RESEARCH.md`, and do not silently broaden the active command set.
