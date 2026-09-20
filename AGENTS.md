# AGENTS.md

## Mission

Build a small, auditable macOS toolchain that lets the owner of a **2013 Ford Focus gasoline vehicle with a blade key** use an **MKS CANable V2.0 Pro** to reproduce the PCM **KOER on-demand self-test** state used by the documented FORScan MyKey-clearing workaround.

The user has lost the admin key. The remaining key is a MyKey. Do **not** implement PATS key erasure/relearning, immobilizer bypass, ECU flashing, arbitrary configuration writes, As-Built writes, ECU resets, or SecurityAccess. The target is only the ordinary PCM KOER service/test needed before the owner performs the cluster's normal `Clear All MyKeys` action.

## Known hardware/environment

- Host: macOS
- CAN adapter: MKS CANable V2.0 Pro
- CANable firmware observed live: `16e7497-dirty`
- Observed device: `/dev/cu.usbmodem207E38A238461`
- HS-CAN bitrate: 500000 bit/s
- Vehicle OBD-II pin 6 -> CANable CANH
- Vehicle OBD-II pin 14 -> CANable CANL
- Vehicle OBD-II pin 5 -> CANable GND
- CANable powered by USB
- Do not connect OBD-II pin 16 (+12 V)
- Leave CANable 120R termination disabled on the in-vehicle bus

The serial device name can change after reconnect/reboot, so scripts must support `CANABLE_PORT` and auto-detection rather than assuming the observed path forever.

## Verified live vehicle state

The following has been verified against the actual vehicle. The VIN is intentionally not stored in this public repository.

```text
CANable:          detected and responding
Firmware:         16e7497-dirty
HS-CAN:           active at 500 kbit/s
Passive capture:  successful
PCM calibration:  HFCR3PS.H32
ECU name:         ECM.-EngineControl
PCM request ID:   0x7E0 confirmed
PCM response ID:  0x7E8 confirmed
Protocol:         UDS / ISO-TP confirmed
Default session:  10 01 -> 50 01 confirmed
P2ServerMax:      50 ms
P2*ServerMax:     5000 ms
```

Exact live session exchange:

```text
TX 0x7E0: 10 01
RX 0x7E8: 50 01 00 32 01 F4
```

The fallback legacy/KWP request `10 81` was not sent.

No KOER, PATS, SecurityAccess, configuration writes, ECU resets, As-Built changes, downloads, uploads, key programming, or firmware programming operations have been performed.

## Current research state

The protocol family is no longer ambiguous: this PCM uses UDS-style diagnostic-session semantics.

Older Ford/KWP research used:

```text
31 02 00
33 02 00
```

Those bytes are **retired as executable KOER candidates for this PCM**.

For UDS, service `0x31` is `RoutineControl`:

```text
31 01 RR RR ...   startRoutine
31 02 RR RR ...   stopRoutine
31 03 RR RR ...   requestRoutineResults
71 xx RR RR ...   positive response
```

Ford-family material provides a credible clue that RoutineIdentifier `0x0202` is commonly named **On-Demand Self-Test**, but there is not yet sufficient Focus/PCM-specific evidence to transmit `31 01 02 02` to calibration `HFCR3PS.H32`.

Do **not** translate the old command into `31 01 02 02` and send it simply because the UDS structure is known.

See `docs/KOER_RESEARCH.md` for the evidence and gaps.

## Working principles

1. **Passive first.** Verify adapter, bitrate and traffic in silent/listen-only mode before transmitting.
2. **Identify before controlling.** Prefer read-only OBD/UDS identification before invoking any routine.
3. **No guessed routines.** Never brute-force or enumerate RoutineIdentifiers against the vehicle.
4. **No persistent changes.** Never send configuration writes, As-Built writes, PATS commands, SecurityAccess, ECUReset, RequestDownload/Upload, TransferData, or firmware operations.
5. **No vehicle firmware flashing.** This project is not an ECU flasher.
6. **Do not flash the CANable unless needed.** The adapter already works with firmware `16e7497-dirty`.
7. **Guard active scripts.** Any state-changing diagnostic request must default to dry-run and require an explicit `--execute` flag.
8. **Log everything.** Every active CAN request should print timestamp, arbitration ID and payload. Capture responses before adding higher-level interpretation.
9. **Keep code small.** Prefer transparent Python and SLCAN/ISO-TP helpers over a large framework.
10. **Never commit the VIN.** This repository is public.
11. **Do not assume cross-platform Ford evidence is exact PCM evidence.** FG Falcon, newer Ford modules, Jaguar/Land Rover, etc. can provide supporting clues only.

## Goal state

A command similar to this should eventually be possible:

```bash
source .venv/bin/activate
python scripts/koer.py --execute
```

It should:

1. verify adapter and HS-CAN connection;
2. confirm PCM identification/addressing;
3. establish the evidence-backed diagnostic session;
4. verify engine-running/vehicle-stopped preconditions where possible;
5. initiate only the verified PCM KOER routine;
6. handle response-pending/result retrieval correctly;
7. never touch PATS, security, programming, configuration, or resets;
8. tell the user when KOER has completed;
9. stop transmitting before the user performs the manual foil / ignition-cycle / `Clear All MyKeys` procedure.

## Current scripts

```bash
bash scripts/setup.sh                    # create venv/install project
bash scripts/detect_canable.sh           # find likely /dev/cu.* port
python scripts/probe_canable.py          # query SLCAN firmware version
python scripts/listen_hscan.py           # 500 kbps silent capture
python scripts/capture_hscan.py          # timestamped capture to logs/
python scripts/pcm_obd_probe.py          # standard PCM OBD-II PID probe
python scripts/identify_pcm.py           # Mode 09 identification
python scripts/probe_diag_session.py     # guarded diagnostic-session probe
python scripts/diag_request.py           # guarded generic ISO-TP request runner
```

Transport tests:

```bash
python -m unittest discover -s tests -v
```

There is a known pre-existing malformed SLCAN unit-test fixture where DLC declares 8 bytes but only 6 payload bytes are present. Fix the fixture/expectation after inspecting intent, then get the full test suite green before adding new active vehicle commands.

## Immediate agent tasks

### Task 1, update from repository and preserve verified facts

```bash
git pull
bash scripts/setup.sh
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Treat these as verified unless contradictory live evidence appears:

```text
HFCR3PS.H32
ECM.-EngineControl
0x7E0 -> 0x7E8
UDS default session accepted
P2 = 50 ms
P2* = 5000 ms
```

Do not store the VIN in tracked files.

### Task 2, fix the existing unit-test fixture

Inspect the malformed SLCAN fixture. If DLC is 8, provide 8 payload bytes; if the test intends a 6-byte frame, change DLC to 6. Preserve the behavioral intent rather than merely weakening parsing validation.

Run the entire suite afterward.

### Task 3, collect additional read-only PCM identifiers

The next live work should remain read-only.

Implement a small guarded script such as:

```text
scripts/read_pcm_ids.py
```

Use UDS `ReadDataByIdentifier (0x22)` to request a deliberately small set of documented/standard identification DIDs useful for matching Ford diagnostic-definition data.

Research each DID before adding it. Candidate identification areas include manufacturer/software/hardware identifiers in the `F18x/F19x` range.

Requirements:

- show the exact DID and expected meaning before transmitting;
- read-only requests only;
- use `0x7E0 -> 0x7E8`;
- remain in the already-confirmed default session unless evidence requires otherwise;
- tolerate `7F 22 31` or other unsupported/read-condition NRCs and continue safely;
- print raw and printable/decoded values;
- avoid printing/storing VIN or ECU serial number in tracked docs;
- no SecurityAccess;
- no session changes during this script;
- no writes/resets/routines/programming.

Useful outputs would be Ford software number, software version, ECU hardware number, supplier software/hardware number, application/strategy identifier, or system/engine type.

Update `docs/KOER_RESEARCH.md` with only non-sensitive identifiers that materially help protocol research.

### Task 4, use the additional identifiers to locate exact diagnostic definitions

Search for:

- matching 2012-2014 Focus 2.0L PCM ODX/MDX data;
- IDS/FJDS/FDRS diagnostic metadata;
- Ford engineering/software part numbers;
- strategy/application IDs;
- same-generation Focus FORScan/IDS/FCOM traces;
- exact UDS RoutineIdentifier for PCM On-Demand Self-Test;
- required session and KOER entry criteria.

Supporting evidence that other Ford-family modules use RID `0x0202` is not sufficient on its own.

### Task 5, only after evidence exists, design KOER transaction

Do not execute it yet. First document the exact proposed transaction in `docs/KOER_RESEARCH.md`, including:

- required session request/response;
- `RoutineControl` start request and positive response;
- any request data following the RID;
- NRC `0x78` handling if applicable;
- result-retrieval request if applicable;
- tester-present behavior if applicable;
- timeout derived from confirmed P2/P2* values and routine-specific behavior;
- engine-running/vehicle-stopped and other preconditions.

Only then implement a dry-run-first `scripts/koer.py`.

## User procedure after KOER completes

The future software should stop active diagnostics and display:

```text
KOER completed.
1. Keep the driver's door closed.
2. Wrap the plastic head of the blade key tightly in foil to block its transponder.
3. Turn ignition OFF.
4. Within a few seconds turn ignition back to ON/RUN, do not crank the engine.
5. On the instrument cluster select Settings -> MyKey -> Clear All.
6. Hold OK until the cluster confirms all MyKeys are cleared.
```

Do not automate ignition, immobilizer, key programming, or cluster configuration.

## Coding conventions

- Python 3.11+
- type hints on public helpers
- no hidden background threads unless required for serial receive
- keep CAN/ISO-TP parsing testable using byte strings and prerecorded frames
- reusable code in `focus_can/`
- executable workflows in `scripts/`
- add unit tests for new parsing/session/response logic
- use `CANABLE_PORT` when present
- never assume wire colors, only OBD pin numbers
- never commit VIN or unnecessary unique vehicle identifiers

## Commands for an agent to start immediately

```bash
git pull
bash scripts/setup.sh
source .venv/bin/activate
python -m unittest discover -s tests -v
```

Fix the known malformed SLCAN fixture first.

Then implement/review the **read-only** PCM identification-DID script. Before running it live, print the exact list of DIDs, their expected meanings, and the exact `22 xx xx` requests that will be transmitted.

Do not send any `0x31` RoutineControl request until `docs/KOER_RESEARCH.md` contains Focus/PCM-specific evidence for the KOER RID and required session.
