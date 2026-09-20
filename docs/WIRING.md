# Wiring

## HS-CAN connection

For the initial PCM work, use only the Focus high-speed CAN bus:

| OBD-II pin | Vehicle signal | MKS CANable V2.0 Pro |
|---:|---|---|
| 6 | HS-CAN High | `CANH` |
| 14 | HS-CAN Low | `CANL` |
| 5 | Signal ground | `GND` |

The CANable is powered from USB.

**Do not connect OBD-II pin 16 (+12 V) to this CANable setup.**

Leave the CANable's switchable **120 ohm termination disabled** while attached to the vehicle. The installed vehicle bus already has its normal termination.

## Connector orientation

Vehicle-side female DLC, looking straight into the socket under the dash:

```text
        wider edge
   ___________________
  / 1  2  3  4  5  6  7  8 \
 /  9 10 11 12 13 14 15 16  \
 -----------------------------
```

A male pigtail viewed from its **mating/pin face** is mirrored:

```text
        wider edge
   ___________________
  / 8  7  6  5  4  3  2  1 \
 / 16 15 14 13 12 11 10  9  \
 -----------------------------
```

For the male pigtail used in this project, identify the wires connected to pins **6, 14 and 5** with a multimeter in continuity mode before connecting the CANable. Generic pigtail wire colors are not authoritative.

## Other Ford CAN pins

Pins 3/11 are commonly used for Ford MS-CAN on this generation, but they are **not part of the initial PCM KOER path**. Do not connect them during the HS-CAN bring-up.

## Bring-up order

1. CANable connected to Mac by USB only.
2. Run `python scripts/probe_canable.py`.
3. With the car off, connect OBD pin 6 -> CANH, pin 14 -> CANL, pin 5 -> GND.
4. Turn ignition ON.
5. Run `python scripts/listen_hscan.py` in silent mode.
6. Only after passive frames are visible, run `python scripts/pcm_obd_probe.py`.
