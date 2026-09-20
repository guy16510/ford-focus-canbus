# AGENTS.md

## Mission

Build a small, auditable macOS toolchain that lets the owner of a **2013 Ford Focus gasoline vehicle with a blade key** use an **MKS CANable V2.0 Pro** to reproduce the PCM **KOER on-demand self-test** state used by the documented FORScan MyKey-clearing workaround.

The user has lost the admin key. The remaining key is a MyKey. Do **not** implement PATS key erasure/relearning, immobilizer bypass, ECU flashing, or arbitrary configuration writes. The target is only the ordinary PCM KOER service/test needed before the owner performs the cluster's normal `Clear All MyKeys` action.

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
```

`identify_pcm.py` successfully read standard Mode 09 identification data over `0x7E0 -> 0x7E8`. A previous one-off Mode 01 PID 00 timeout should **not** be treated as evidence that the addressing is wrong.

No KOER, PATS, SecurityAccess, configuration writes, ECU resets, As-Built changes, downloads, uploads, or programming operations have been performed.

## Current research state

There is strong Ford-specific evidence that at least some Ford diagnostic implementations use local routine `0x0202` for **On-Demand Self-Test**:

```text
candidate start:       31 02 00
candidate positive:    71 02 00 ...
candidate result poll: 33 02 00
candidate pending:     7F 33 78
candidate result:      73 02 00 ...
```

This is **not yet authorization to transmit those bytes to this PCM**. The remaining blocker is identifying the diagnostic-session semantics used by calibration `HFCR3PS.H32` and validating that the `0x0202` routine mapping applies to this PCM.

The next live step is `scripts/probe_diag_session.py`, which only tests diagnostic-session control and defaults to dry-run.

## Working principles

1. **Passive first.** Verify adapter, bitrate and traffic in silent/listen-only mode before transmitting.
2. **Identify before controlling.** Confirm PCM request/response addressing using harmless OBD-II reads before any enhanced diagnostic request.
3. **No guessed writes.** Never send a configuration write, security-access request, PATS command, ECU reset, firmware transfer, or unverified routine-control request.
4. **No vehicle firmware flashing.** This project is not an ECU flasher.
5. **Do not flash the CANable unless needed.** The adapter is already enumerating and responding with SLCAN firmware `16e7497-dirty`.
6. **Guard active scripts.** Any enhanced diagnostic command must default to dry-run and require an explicit `--execute` flag.
7. **Log everything.** Every active CAN request should print timestamp, arbitration ID and payload. Capture responses before adding higher-level interpretation.
8. **Keep code small.** Prefer transparent Python and SLCAN/ISO-TP helpers over a large framework.
9. **Never commit the VIN.** This repository is public.

## Goal state

A command similar to this should eventually be possible:

```bash
source .venv/bin/activate
python scripts/koer.py --execute
```

It should:

1. verify the adapter and HS-CAN connection;
2. confirm the PCM is responding;
3. verify engine-running preconditions where possible;
4. initiate the **PCM Key On Engine Running On Demand Self Test** using a payload verified for this vehicle/protocol;
5. monitor responses until the test completes or times out;
6. never touch PATS or module programming;
7. tell the user when to wrap the key head in foil, turn ignition off, and quickly return ignition to ON without starting;
8. then stop transmitting so the user can select `Settings -> MyKey -> Clear All` in the cluster.

## Current scripts

```bash
bash scripts/setup.sh                    # create venv/install project
bash scripts/detect_canable.sh           # find likely /dev/cu.* port
python scripts/probe_canable.py          # close channel + query SLCAN firmware version
python scripts/listen_hscan.py           # 500 kbps silent capture
python scripts/capture_hscan.py          # timestamped capture to logs/
python scripts/pcm_obd_probe.py          # harmless PCM OBD-II PID probe
python scripts/identify_pcm.py           # VIN/calibration/ECU identification via Mode 09
python scripts/probe_diag_session.py     # guarded diagnostic-session compatibility probe
python scripts/diag_request.py           # guarded generic ISO-TP request runner
```

Transport tests:

```bash
python -m unittest discover -s tests -v
```

## Immediate agent tasks

### Task 1, verify the existing environment

Do not redo completed research blindly. Pull the repository and inspect the current state first:

```bash
git pull
bash scripts/setup.sh
source .venv/bin/activate
python -m unittest discover -s tests -v
bash scripts/detect_canable.sh
python scripts/probe_canable.py
```

Known-good result: adapter responds, firmware `16e7497-dirty`.

### Task 2, preserve the already-verified PCM facts

These are already established live and should be treated as known facts unless new evidence contradicts them:

```text
Calibration ID:  HFCR3PS.H32
ECU:             ECM.-EngineControl
PCM TX:          0x7E0
PCM RX:          0x7E8
```

Do not store or print the VIN into tracked files.

### Task 3, determine diagnostic-session semantics

Run dry-run first:

```bash
python scripts/probe_diag_session.py
```

With ignition ON and only after reviewing the script:

```bash
python scripts/probe_diag_session.py --execute
```

The script is intentionally narrow:

1. send `10 01` to `0x7E0`;
2. if positive response `50 01` arrives, stop and treat UDS-style session control as confirmed;
3. only if `10 01` returns a diagnostic negative response to service `0x10`, try legacy Ford/KWP-style `10 81`;
4. if `50 81` arrives, legacy Ford/KWP session semantics are strongly supported;
5. on timeout or unexpected response, stop instead of guessing.

Do not add KOER transmission to this probe.

Record the exact TX/RX bytes and outcome in `docs/KOER_RESEARCH.md`.

### Task 4, validate On-Demand Self-Test for this exact PCM

Only after Task 3 identifies the session model, research/capture evidence that calibration/family `HFCR3PS.H32` uses local routine `0x0202` for PCM On-Demand Self-Test.

Evidence preference:

1. a trace from FORScan/IDS/FCOM performing PCM KOER on a same-generation Focus;
2. Ford diagnostic documentation tied to the matching PCM/calibration family;
3. matching Ford diagnostic definition data;
4. a reputable implementation with matching protocol/platform;
5. controlled read-only/live diagnostic interrogation that does not mutate configuration or security state.

Document all evidence in `docs/KOER_RESEARCH.md` before adding an executable KOER command.

### Task 5, implement `scripts/koer.py`

Only after the session and routine are sufficiently validated:

- use `0x7E0 -> 0x7E8` physical addressing;
- use the correct session semantics determined by Task 3;
- implement ISO-TP correctly, including multi-frame responses and flow control;
- maintain tester-present/keepalive only if evidence shows it is required;
- explicitly handle negative-response codes and response-pending behavior;
- require `--execute`;
- default to dry-run;
- log every request/response;
- terminate cleanly on Ctrl-C;
- never fall through to a different routine when a request fails;
- never request SecurityAccess;
- never touch PATS or configuration writes;
- include a clearly visible user prompt when KOER completes.

## User procedure after KOER completes

The software should stop active diagnostics and display something equivalent to:

```text
KOER completed.
1. Keep the driver's door closed.
2. Wrap the plastic head of the blade key tightly in foil to block its transponder.
3. Turn ignition OFF.
4. Within a few seconds turn ignition back to ON/RUN, do not crank the engine.
5. On the instrument cluster select Settings -> MyKey -> Clear All.
6. Hold OK until the cluster confirms all MyKeys are cleared.
```

Do not automate ignition, immobilizer, or cluster configuration.

## Coding conventions

- Python 3.11+
- Type hints on public helpers
- No hidden background threads unless required for serial receive
- Keep CAN/ISO-TP parsing testable using byte strings and prerecorded frames
- Put reusable code in `focus_can/`
- Put executable workflows in `scripts/`
- Add unit tests for parsing before adding new active vehicle commands
- Use `CANABLE_PORT` environment variable when present
- Never assume wire colors, only OBD pin numbers
- Never commit the VIN or other unnecessary vehicle identifiers

## Commands for an agent to start immediately

```bash
git pull
bash scripts/setup.sh
source .venv/bin/activate
python -m unittest discover -s tests -v
python scripts/probe_canable.py
python scripts/probe_diag_session.py
```

Review the dry-run output and script. If the vehicle is connected, ignition is ON, and the user approves the narrow session probe:

```bash
python scripts/probe_diag_session.py --execute
```

Then use the returned bytes to update `docs/KOER_RESEARCH.md` and continue only with evidence-backed KOER research. Do not expand scope into PATS, key programming, immobilizer bypass, As-Built writes, or module firmware.
