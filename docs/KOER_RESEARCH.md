# KOER research notebook

## Objective

Identify the exact diagnostic request sequence that a Ford diagnostic tool uses to invoke **PCM Key On Engine Running On Demand Self Test (KOER)** on this specific 2013 Focus PCM.

Do not hard-code a guessed service/routine ID.

## Evidence already established

### Vehicle-specific PCM identification

Verified live against the vehicle over HS-CAN using physical PCM addressing:

```text
PCM calibration:  HFCR3PS.H32
ECU name:         ECM.-EngineControl
PCM request ID:   0x7E0 confirmed
PCM response ID:  0x7E8 confirmed
```

The VIN was intentionally **not committed** because this repository is public.

`identify_pcm.py` successfully retrieved Mode 09 VIN, Calibration ID and ECU Name through `0x7E0 -> 0x7E8`. That means the earlier one-off timeout from `pcm_obd_probe.py` is no longer evidence of an addressing problem. The same physical pair is proven to work. Likely explanations for the earlier miss are ignition/module state, timing, or a transient transport issue.

### FORScan behavior

FORScan documents the user-level sequence as:

`Tests -> PCM Key On Engine Running On Demand Self Test`

and uses that completed test as the first step in the no-admin-key MyKey workaround.

Source: https://forum.forscan.org/viewtopic.php?t=11739

### Ford service description

Ford service documentation describes KOER On-Demand Self-Test as a functional PCM test performed on demand with the engine running and the vehicle stopped. Faults found during the test are returned to a diagnostic tool as DTCs.

This establishes that KOER is a normal PCM diagnostic function, but it does **not** by itself establish the raw request bytes for this PCM.

### Strong Ford routine clue, local routine ID 0x0202

Open-source Ford diagnostic material provides a much stronger lead than a generic UDS guess:

- `ghostdev137/ford-pscm-re` contains multiple extracted Ford diagnostic-definition files where `routine_0202` is explicitly named **On-Demand Self-Test**.
- `jakka351/FG-Falcon`, in `Diagnostic/routineControl_OnDemandSelfTest.cs`, implements Ford On-Demand Self-Test by starting a diagnostic session and sending `31 02 00`. It expects positive response service `71`, then polls results with `33 02 00` until receiving positive response service `73`.
- The same FG-Falcon repository includes captured/self-test examples showing `31 02 00 -> 71 02 00`, followed by `33 02 00`, with `7F 33 78` used while results are still pending.

Relevant repositories/files:

- https://github.com/ghostdev137/ford-pscm-re
- https://github.com/jakka351/FG-Falcon/blob/master/Diagnostic/routineControl_OnDemandSelfTest.cs
- https://github.com/jakka351/FG-Falcon/blob/master/resources/IC_DiagSig_SelfTest.txt

This is **strong evidence that Ford uses local routine ID 0x0202 for On-Demand Self-Test on at least some Ford modules/protocol generations**.

It is **not yet proof** that calibration `HFCR3PS.H32` wants the same diagnostic-session setup or exact `31/33` service sequence. Do not transmit the candidate simply because it is documented here.

Current candidate:

```text
candidate start:       31 02 00
candidate positive:    71 02 00 ...
candidate result poll: 33 02 00
candidate pending:     7F 33 78
candidate result:      73 02 00 ...
```

### Historical Ford diagnostic material

`twhitehead/notes-obd2elm327edb` documents Ford KOEO/KOER scan-tool commands for 1990s EEC-V vehicles. It is useful historical context only. Those payloads and headers are not evidence for this 2013 Focus.

Source: https://github.com/twhitehead/notes-obd2elm327edb

### Third-party Ford diagnostics

FCOM exposes a PCM KOEO/KOER test on Ford vehicles, further confirming that the operation is available through the diagnostic link:

https://www.obdtester.com/fcom-video-tutorials

## What remains unknown

The important remaining question is **diagnostic-session semantics for PCM calibration `HFCR3PS.H32`**.

The candidate Ford implementations enter a diagnostic session before invoking routine `0x0202`, but different Ford generations/modules use different session subfunctions. We still need Focus/PCM-specific evidence for the required session before executing KOER.

Do not probe PATS, SecurityAccess, module programming, ECU reset, As-Built writes, download/upload services, or key functions while resolving this.

## Best paths to the exact KOER command

### Path A, match this calibration/PCM family to diagnostic definitions

Search Ford diagnostic definitions and open-source implementations for calibration `HFCR3PS.H32`, its strategy family, or the matching 2013 Focus gasoline PCM. Determine:

1. required diagnostic session;
2. whether local routine `0x0202` is PCM On-Demand Self-Test;
3. whether start/result services are `0x31` / `0x33`;
4. expected positive and response-pending behavior;
5. KOER entry criteria.

### Path B, capture a known-good diagnostic session

Highest confidence if available. Capture the CAN traffic while FORScan, IDS or FCOM performs only the PCM KOER test on a matching platform. Diff:

1. idle diagnostic session;
2. KOER start;
3. KOER in progress;
4. KOER completion/result retrieval.

Look specifically at `0x7E0 -> 0x7E8` and check for the candidate session setup, `31 02 00`, `33 02 00`, `71`, `73`, and `7F 33 78`.

### Path C, Focus-specific Ford diagnostic documentation

Find documentation tied to the 2013 Focus gasoline PCM / calibration family and record the exact service, local routine identifier, required session and expected responses.

## Validation checklist before `koer.py`

- [x] PCM calibration/strategy identified: `HFCR3PS.H32`
- [x] ECU identified: `ECM.-EngineControl`
- [x] `0x7E0/0x7E8` physical addressing verified live
- [ ] confirm PCM protocol/session semantics for this calibration family
- [ ] verify local routine ID `0x0202` is PCM On-Demand Self-Test for this PCM
- [ ] required diagnostic session identified
- [ ] KOER request bytes supported by Focus-specific evidence
- [ ] any tester-present/keepalive behavior understood
- [ ] positive response format documented
- [ ] response-pending handling documented
- [ ] completion/result query documented
- [ ] engine-running and vehicle-stopped preconditions documented
- [ ] request reproduced first in dry-run output
- [ ] no PATS/security/write/flash service involved

## Candidate implementation shape

Once verified, `scripts/koer.py` should keep protocol constants explicit. If Focus-specific evidence confirms the current candidate, that may look approximately like:

```python
PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
KOER_START = bytes.fromhex("31 02 00")
KOER_RESULTS = bytes.fromhex("33 02 00")
```

Those constants are **research candidates, not authorization to transmit them yet**.

If a diagnostic session is required, show each request separately in logs. Do not hide the traffic behind a large third-party abstraction.
