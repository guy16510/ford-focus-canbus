# 2013 Ford Focus CAN / MyKey Lab

macOS tooling for a **2013 Ford Focus** using an **MKS CANable V2.0 Pro**.

The immediate goal is to reproduce the documented FORScan PCM KOER workflow that can expose the instrument-cluster **Clear MyKeys** option when the only remaining blade key is a MyKey.

## Hardware

- Mac
- MKS CANable V2.0 Pro over USB
- Current observed serial port: `/dev/cu.usbmodem207E38A238461`
- 2013 Ford Focus, gasoline, blade key

### OBD-II wiring, HS-CAN

| Vehicle OBD-II | Signal | CANable |
|---|---|---|
| Pin 6 | HS-CAN High | CANH |
| Pin 14 | HS-CAN Low | CANL |
| Pin 5 | Signal ground | GND |

**Do not connect OBD pin 16 (+12 V) to the CANable.** Power the CANable from USB. Leave the CANable 120 ohm termination disabled when attached to the vehicle.

## Quick start

```bash
./scripts/setup.sh
source .venv/bin/activate
./scripts/detect_canable.sh
python scripts/probe_canable.py
```

After wiring pins 6, 14 and 5 and turning ignition ON:

```bash
python scripts/listen_hscan.py
```

That listener opens the adapter at 500 kbit/s in silent/listen-only mode.

When passive traffic is confirmed, test a normal emissions/PCM request:

```bash
python scripts/pcm_obd_probe.py
```

## MyKey workflow we are targeting

The documented FORScan workaround is:

1. Start the car.
2. Start the PCM `Key On Engine Running On Demand Self Test` (KOER).
3. Wait for KOER to complete.
4. Block the blade key transponder, for example by wrapping the plastic key head in foil.
5. Turn the engine/ignition OFF.
6. Quickly turn ignition back ON without starting the engine.
7. In the cluster, go to MyKey and hold OK on **Clear All MyKeys**.

This repository does **not** hard-code an unverified Ford KOER payload. `scripts/diag_request.py` is a guarded ISO-TP diagnostic runner for testing a verified payload once identified. It dry-runs unless `--execute` is supplied.

## Why not flash the CANable?

Do not flash firmware just because the board is present. The Mac already enumerates this adapter as a USB modem and the MKS CANable V2.0 Pro normally provides the SLCAN command set we need. `probe_canable.py` verifies that first. Firmware changes should only be considered if the SLCAN probe fails and the exact board/MCU/firmware target has been verified.

## Safety

This repo intentionally starts passive. Do not transmit arbitrary CAN frames, write configuration, erase keys, invoke PATS, or flash vehicle modules. The only active traffic currently included is ordinary OBD-II PCM interrogation and an explicit guarded diagnostic request tool.

See [AGENTS.md](AGENTS.md) for the implementation plan and [docs/WIRING.md](docs/WIRING.md) for connector orientation and wiring.
