# KOER research notebook

## Objective

Identify the exact diagnostic request sequence that a Ford diagnostic tool uses to invoke **PCM Key On Engine Running On Demand Self Test (KOER)** on this specific 2013 Focus PCM.

Do not hard-code a guessed service/routine ID.

## Evidence already established

### FORScan behavior

FORScan documents the user-level sequence as:

`Tests -> PCM Key On Engine Running On Demand Self Test`

and uses that completed test as the first step in the no-admin-key MyKey workaround.

Source: https://forum.forscan.org/viewtopic.php?t=11739

### Ford service description

Ford service documentation describes KOER On-Demand Self-Test as a functional PCM test performed on demand with the engine running and the vehicle stopped. Faults found during the test are returned to a diagnostic tool as DTCs.

This establishes that KOER is a normal PCM diagnostic function, but it does **not** establish the raw request bytes for this PCM.

### Strong Ford routine clue, local routine ID 0x0202

Open-source Ford diagnostic material provides a much stronger lead than a generic UDS guess:

- `ghostdev137/ford-pscm-re` contains multiple extracted Ford diagnostic-definition files where `routine_0202` is explicitly named **On-Demand Self-Test**.
- `jakka351/FG-Falcon`, in `Diagnostic/routineControl_OnDemandSelfTest.cs`, implements Ford On-Demand Self-Test by starting a diagnostic session and sending `31 02 00`. It expects positive response service `71`, then polls results with `33 02 00` until receiving positive response service `73`.

Relevant repositories/files:

- https://github.com/ghostdev137/ford-pscm-re
- https://github.com/jakka351/FG-Falcon/blob/master/Diagnostic/routineControl_OnDemandSelfTest.cs

This is **strong evidence that Ford uses local routine ID 0x0202 for an On-Demand Self-Test on at least some Ford modules/protocol generations**.

It is **not yet proof** that this exact 2013 Focus PCM wants the byte sequence `31 02 00` / `33 02 00`, or which diagnostic session it requires. The FG Falcon implementation is a different Ford platform, and Ford diagnostic definitions span multiple modules and model years.

Treat the current candidate as:

```text
candidate start:       31 02 00
candidate positive:    71 ...
candidate result poll: 33 02 00
candidate result:      73 ...
```

Do not transmit the candidate simply because it exists here. First identify the Focus PCM/calibration and establish that its protocol/session semantics match.

### Historical Ford diagnostic material

`twhitehead/notes-obd2elm327edb` documents Ford KOEO/KOER scan-tool commands for 1990s EEC-V vehicles. It is useful for understanding Ford's history of exposing on-demand tests to diagnostic tools, but those old payloads and headers are not evidence for a 2013 Focus.

Source: https://github.com/twhitehead/notes-obd2elm327edb

### Third-party Ford diagnostics

FCOM exposes a PCM KOEO/KOER test on Ford vehicles, further confirming that the operation is available through the diagnostic link:

https://www.obdtester.com/fcom-video-tutorials

## Do not copy old Ford payloads blindly

Older Ford service literature includes enhanced-diagnostic strings used to initiate KOER on much older EEC-V platforms. Those are useful historical clues only. They are not evidence that a 2013 Focus uses the same request sequence.

Likewise, even though `31 02 00` / `33 02 00` is now a credible Ford candidate, the exact session, service semantics, routine identifier, preconditions and response handling must be verified against this Focus PCM before execution.

## Vehicle-specific data to collect

Run:

```bash
python scripts/pcm_obd_probe.py
python scripts/identify_pcm.py
```

Record below:

```text
VIN:              TODO, do not commit while repo is public
PCM calibration:  TODO
ECU name:         TODO
PCM request ID:   expected 0x7E0, verify
PCM response ID:  expected 0x7E8, verify
```

Do not commit the VIN if the repository remains public. Calibration/strategy identifiers are enough for most protocol research.

## Best paths to the exact KOER command

### Path A, identify this PCM, then match diagnostic definitions

Use `identify_pcm.py` to get calibration/ECU identification. Search Ford diagnostic definitions and open-source implementations for that PCM family and determine whether the module defines local routine `0x0202` as On-Demand Self-Test and which session is required.

### Path B, capture a known-good diagnostic session

Highest confidence if available. Capture the CAN traffic while FORScan, IDS or FCOM performs only the PCM KOER test on a matching platform. Diff:

1. idle diagnostic session;
2. KOER start;
3. KOER in progress;
4. KOER completion/result retrieval.

Look for new traffic involving the PCM physical diagnostic IDs and reconstruct ISO-TP payloads. Specifically check whether `31 02 00`, `33 02 00`, positive `71`, and positive `73` appear.

### Path C, exact Ford diagnostic documentation

Find documentation tied to the PCM calibration/strategy returned by `identify_pcm.py`. Record the service, local routine identifier, required diagnostic session and expected positive/negative responses.

### Path D, matching open-source implementation

A matching implementation is acceptable only if vehicle generation/protocol/PCM family line up. Record repository, commit and relevant code path here before using it live.

## Validation checklist before `koer.py`

- [ ] PCM calibration/strategy identified
- [ ] 0x7E0/0x7E8 physical addressing verified or corrected
- [ ] determine whether PCM uses Ford local-routine services matching `31/33` semantics
- [ ] verify whether local routine ID `0x0202` is On-Demand Self-Test for this PCM
- [ ] required diagnostic session identified
- [ ] KOER request bytes supported by Focus-specific evidence
- [ ] any tester-present/keepalive behavior understood
- [ ] positive response format documented
- [ ] response-pending NRC handling documented if applicable
- [ ] completion/result query documented if separate from start
- [ ] engine-running and vehicle-stopped preconditions documented
- [ ] request reproduced first in dry-run output
- [ ] no PATS/security/write/flash service involved

## Candidate implementation shape

Once verified, add `scripts/koer.py` and keep the protocol constants explicit. If the Focus-specific evidence confirms the current candidate, that may look approximately like:

```python
PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
KOER_START = bytes.fromhex("31 02 00")
KOER_RESULTS = bytes.fromhex("33 02 00")
```

Those constants are **research candidates, not authorization to transmit them yet**.

If a diagnostic session is required, show each request separately in logs. Do not hide the traffic behind a large third-party abstraction.
