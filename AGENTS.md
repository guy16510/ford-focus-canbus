# AGENTS.md

## Mission

Build a small, auditable macOS toolchain that lets the owner of a **2013 Ford Focus gasoline vehicle with a blade key** use an **MKS CANable V2.0 Pro** to reproduce the PCM **KOER on-demand self-test** state used by the documented FORScan MyKey-clearing workaround.

The user has lost the admin key. The remaining key is a MyKey. Do **not** implement PATS key erasure/relearning, immobilizer bypass, ECU flashing, or arbitrary configuration writes. The target is only the ordinary PCM KOER service/test needed before the owner performs the cluster's normal `Clear All MyKeys` action.

## Known hardware/environment

- Host: macOS
- CAN adapter: MKS CANable V2.0 Pro
- Observed device: `/dev/cu.usbmodem207E38A238461`
- HS-CAN bitrate: 500000 bit/s
- Vehicle OBD-II pin 6 -> CANable CANH
- Vehicle OBD-II pin 14 -> CANable CANL
- Vehicle OBD-II pin 5 -> CANable GND
- CANable powered by USB
- Do not connect OBD-II pin 16 (+12 V)
- Leave CANable 120R termination disabled on the in-vehicle bus

The serial device name can change after reconnect/reboot, so scripts must support `CANABLE_PORT` and auto-detection rather than assuming the observed path forever.

## Working principles

1. **Passive first.** Verify adapter, bitrate and traffic in silent/listen-only mode before transmitting.
2. **Identify before controlling.** Confirm PCM request/response addressing using a harmless OBD-II request before any enhanced diagnostic request.
3. **No guessed writes.** Never send a configuration write, security-access request, PATS command, ECU reset, firmware transfer, or unverified routine-control request.
4. **No vehicle firmware flashing.** This project is not an ECU flasher.
5. **Do not flash the CANable unless needed.** The board already enumerates as USB serial. Verify `V` and SLCAN behavior first.
6. **Guard active scripts.** Any enhanced diagnostic command must default to dry-run and require an explicit `--execute` flag.
7. **Log everything.** Every active CAN request should print timestamp, arbitration ID and payload. Capture responses before adding higher-level interpretation.
8. **Keep code small.** Prefer transparent Python and SLCAN/ISO-TP helpers over a large framework.

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
./scripts/setup.sh               # create venv/install dependencies
./scripts/detect_canable.sh      # find likely /dev/cu.* port
python scripts/probe_canable.py  # close channel + query SLCAN firmware version
python scripts/listen_hscan.py   # 500 kbps silent capture
python scripts/capture_hscan.py  # timestamped capture to logs/
python scripts/pcm_obd_probe.py  # harmless PCM OBD-II PID probe
python scripts/diag_request.py   # guarded generic ISO-TP request runner
```

## Immediate agent tasks

### Task 1, establish the physical/transport layer

Run:

```bash
./scripts/setup.sh
source .venv/bin/activate
./scripts/detect_canable.sh
python scripts/probe_canable.py
```

Expected: the adapter returns a response to SLCAN `V`.

With vehicle wired and ignition ON, run:

```bash
python scripts/listen_hscan.py
```

Expected: standard 11-bit CAN frames at 500 kbps. Do not proceed if the bus is silent or errors are returned.

### Task 2, confirm PCM addressing

Run:

```bash
python scripts/pcm_obd_probe.py
```

The script sends a standard OBD-II Mode 01 PID 00 request to physical PCM address `0x7E0` and expects a response at `0x7E8`. Confirm that before adding enhanced diagnostics.

### Task 3, identify the exact KOER request

Do not guess. Research/capture the exact request sequence used by Ford/FORScan for this generation Focus PCM. Likely areas to investigate include Ford enhanced diagnostics over ISO-TP and, if applicable, UDS RoutineControl, but the service and routine identifier must be evidence-backed for the vehicle.

Useful evidence sources, in priority order:

1. a trace from FORScan/IDS/FCOM running the exact PCM KOER test on a same-generation Focus;
2. Ford diagnostic documentation for the exact PCM/protocol;
3. a reputable open-source implementation with matching platform/protocol;
4. controlled live interrogation that only reads supported services/routines and does not mutate state.

Document evidence in `docs/KOER_RESEARCH.md` before hard-coding a command.

### Task 4, implement `scripts/koer.py`

Only after Task 3 is complete:

- use physical PCM addressing;
- implement ISO-TP correctly, including multi-frame responses and flow control;
- maintain any required diagnostic session/keepalive;
- handle negative-response codes explicitly;
- require `--execute`;
- log all request/response frames;
- terminate cleanly on Ctrl-C;
- never fall through to a different routine when a request fails;
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

## Commands for an agent to start immediately

```bash
git pull
./scripts/setup.sh
source .venv/bin/activate
./scripts/detect_canable.sh
python scripts/probe_canable.py
python scripts/listen_hscan.py
```

Then, only after passive traffic is verified:

```bash
python scripts/pcm_obd_probe.py
```

If that succeeds, focus all further work on identifying and validating the exact **PCM KOER on-demand self-test request sequence**. Do not expand scope into PATS, key programming, or module firmware.
