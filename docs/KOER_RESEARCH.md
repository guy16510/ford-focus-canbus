# KOER research notebook

## Objective

Identify the exact diagnostic request sequence that a Ford diagnostic tool uses to invoke **PCM Key On Engine Running On Demand Self Test (KOER)** on this specific 2013 Focus PCM.

Do not hard-code or transmit a guessed service/routine ID.

## Verified vehicle-specific facts

Live verification against the vehicle over HS-CAN established:

```text
PCM calibration:  HFCR3PS.H32
ECU name:         ECM.-EngineControl
PCM request ID:   0x7E0 confirmed
PCM response ID:  0x7E8 confirmed
Protocol:         UDS / ISO-TP on CAN confirmed
```

The VIN is intentionally **not committed** because this repository is public.

`identify_pcm.py` successfully retrieved standard Mode 09 VIN, Calibration ID and ECU Name through `0x7E0 -> 0x7E8`. A prior one-off Mode 01 PID 00 timeout is not evidence of incorrect addressing.

### Confirmed UDS DiagnosticSessionControl semantics

The reviewed session probe was run against the live PCM with ignition ON. It sent only the UDS default-session request and stopped on the positive response:

```text
TX 0x7E0: 10 01
RX 0x7E8: 50 01 00 32 01 F4
```

This confirms UDS-style `DiagnosticSessionControl` semantics for calibration `HFCR3PS.H32`.

Decoded timing values:

```text
50 01       positive response to defaultSession
00 32       P2ServerMax = 0x0032 = 50 ms
01 F4       P2*ServerMax = 0x01F4 * 10 ms = 5000 ms
```

The fallback legacy/KWP-style `10 81` request was **not** sent.

No KOER, PATS, SecurityAccess, configuration write, ECU reset, As-Built change, download/upload, firmware programming, or key operation has been performed.

## FORScan behavior

FORScan documents the user-level sequence as:

`Tests -> PCM Key On Engine Running On Demand Self Test`

and uses completion of that test as the first step in the no-admin-key MyKey workaround.

Source: https://forum.forscan.org/viewtopic.php?t=11739

Ford service descriptions also establish KOER On-Demand Self-Test as a normal PCM diagnostic operation performed with the engine running and vehicle stopped. This establishes the existence of the operation, not its raw UDS request for this PCM.

## Ford routine clue: 0x0202

There is substantial Ford-family evidence associating identifier `0x0202` with **On-Demand Self-Test**, but none located yet is specific enough to authorize transmitting it to PCM calibration `HFCR3PS.H32`.

### Supporting evidence

- `ghostdev137/ford-pscm-re` contains extracted Ford diagnostic-definition material where `routine_0202` is named **On-Demand Self-Test**.
- `jakka351/FG-Falcon` implements an older Ford diagnostic form of On-Demand Self-Test using legacy local-routine services. This is useful for Ford naming/history but is **not the protocol used by this PCM**.
- Later Ford diagnostic-definition material publicly mirrored online also describes UDS RoutineIdentifier `0x0202` as On-Demand Self-Test for other Ford modules and requires an extended diagnostic session.
- Jaguar/Land Rover service information also refers to `On Demand Self Test (0x0202)`, including for PCM-related diagnostics. This is supporting Ford-family/JLR convention evidence only, not 2013 Focus proof.

### Important historical ambiguity

Older Ford Global Diagnostic Specification material also uses `0x0202` as a **PID** meaning the number of trouble codes set due to a diagnostic test. That historical PID must not be conflated with a modern UDS `RoutineIdentifier` merely because the numeric value is the same.

## Legacy candidate retired for this PCM

Before UDS was confirmed, research tracked this legacy Ford/KWP-style candidate:

```text
31 02 00
33 02 00
```

Those bytes are now **retired as executable candidates for this PCM**.

For a UDS ECU, service `0x31` is `RoutineControl` and uses a subfunction followed by a two-byte RoutineIdentifier:

```text
31 01 RR RR ...   startRoutine
31 02 RR RR ...   stopRoutine
31 03 RR RR ...   requestRoutineResults

71 xx RR RR ...   positive response
```

Therefore `31 02 00` is not a complete UDS KOER start request, and legacy service `0x33` result semantics must not be used here.

If future Focus-specific evidence proves that UDS RoutineIdentifier `0x0202` is KOER for `HFCR3PS.H32`, the **shape** of a UDS transaction would be approximately:

```text
CANDIDATE SHAPE ONLY - DO NOT TRANSMIT

31 01 02 02 ...   startRoutine(0x0202)
71 01 02 02 ...   positive start response

31 03 02 02 ...   requestRoutineResults(0x0202)
71 03 02 02 ...   positive result response
```

This is protocol structure, **not evidence that RID `0x0202` is correct for this PCM**.

## What remains unknown

The protocol family is no longer unknown. The remaining blockers are narrower:

1. Which diagnostic session is required for PCM KOER, default `0x01`, extended `0x03`, or another supported session?
2. Does PCM calibration/family `HFCR3PS.H32` map On-Demand Self-Test to UDS RoutineIdentifier `0x0202`?
3. What exact request data, if any, follows the RID?
4. Does the routine return immediately, use NRC `0x78` response-pending, or require explicit `requestRoutineResults` polling?
5. Is `TesterPresent` required while the test runs?
6. What exact entry criteria apply: engine running, RPM range, coolant temperature, transmission state, brake state, vehicle speed, accessory loads, etc.?
7. What marks successful completion versus a routine-completed response carrying on-demand DTCs?

Do not answer these by brute-force routine enumeration against the live vehicle.

## Best next paths

### Path A, identify the PCM more precisely with read-only UDS data

Use `ReadDataByIdentifier (0x22)` only for documented/standard identification DIDs that can help map this PCM to Ford diagnostic-definition data. Useful areas to research include manufacturer software/hardware identifiers in the `F18x/F19x` ranges.

Requirements:

- read-only requests only;
- log exact TX/RX bytes;
- tolerate unsupported-DID NRCs;
- no SecurityAccess;
- no writes;
- no ECUReset;
- no session change unless separately reviewed;
- do not commit VIN or serial-number values unnecessarily.

The goal is to obtain a Ford software number, hardware number, strategy/application identifier, or engine/system identifier that is more searchable than `HFCR3PS.H32`.

### Path B, obtain a same-generation Focus KOER trace

Highest-confidence evidence would be a CAN trace from FORScan, IDS, FCOM, or another known-good tool performing only PCM KOER on a matching 2012-2014 Focus gasoline powertrain.

Reconstruct UDS payloads exchanged on:

```text
0x7E0 -> 0x7E8
```

Look for:

- diagnostic session transition, especially `10 03` / `50 03` if used;
- `31 01 <RID>` startRoutine;
- `71 01 <RID>` positive response;
- NRC `7F 31 78` if response-pending is used;
- `31 03 <RID>` result retrieval if used;
- tester-present traffic `3E` if used.

### Path C, find matching Ford ODX/MDX/IDS diagnostic definitions

Search by identifiers obtained from Path A, not only by `HFCR3PS.H32`. Prefer exact matching PCM family/model-year definitions over generic Ford module examples.

Useful artifacts may include:

- Ford ODX/MDX diagnostic definitions;
- IDS/FJDS/FDRS diagnostic metadata;
- Ford engineering/software part numbers;
- strategy/application IDs;
- matching ECU software/hardware numbers;
- same-generation Focus diagnostic traces.

## Validation checklist before `koer.py`

- [x] PCM calibration identified: `HFCR3PS.H32`
- [x] ECU identified: `ECM.-EngineControl`
- [x] `0x7E0/0x7E8` physical addressing verified live
- [x] UDS DiagnosticSessionControl semantics verified live
- [x] default session `10 01 -> 50 01` verified
- [x] P2/P2* timing decoded as 50 ms / 5000 ms
- [x] legacy `31 02 00` / `33 02 00` candidate retired for this PCM
- [ ] additional read-only PCM software/hardware identifiers collected
- [ ] exact required KOER diagnostic session identified
- [ ] UDS KOER RoutineIdentifier identified with Focus/PCM-specific evidence
- [ ] request data following RID documented, if any
- [ ] positive response format documented
- [ ] response-pending behavior documented
- [ ] completion/result retrieval behavior documented
- [ ] tester-present requirement understood
- [ ] engine-running and other entry criteria documented
- [ ] proposed KOER request reproduced first in dry-run output
- [ ] no PATS/security/write/reset/flash service involved

## Candidate implementation shape

Do not add executable KOER bytes until the RID/session are evidence-backed for this PCM.

Once verified, `scripts/koer.py` should keep protocol constants and session transitions explicit rather than hiding them behind a large abstraction. A future implementation may resemble:

```python
PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
KOER_SESSION = ...      # VERIFIED before use
KOER_ROUTINE_ID = ...   # VERIFIED before use
```

It must default to dry-run and require `--execute` for any live routine invocation.
