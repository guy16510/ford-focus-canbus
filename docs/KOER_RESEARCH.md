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

### Third-party Ford diagnostics

FCOM exposes a PCM KOEO/KOER test on Ford vehicles, further confirming that the operation is available through the diagnostic link:

https://www.obdtester.com/fcom-video-tutorials

## Do not copy old Ford payloads blindly

Older Ford service literature includes enhanced-diagnostic strings used to initiate KOER on much older EEC-V platforms. Those are useful historical clues only. They are not evidence that a 2013 Focus uses the same request sequence.

Likewise, a generic UDS `RoutineControl (0x31)` assumption is not enough. If this PCM uses UDS or a Ford-specific CAN diagnostic protocol, the exact session, service, routine identifier, preconditions and response handling must be verified.

## Vehicle-specific data to collect

Run:

```bash
python scripts/pcm_obd_probe.py
python scripts/identify_pcm.py
```

Record below:

```text
VIN:              TODO
PCM calibration:  TODO
ECU name:         TODO
PCM request ID:   expected 0x7E0, verify
PCM response ID:  expected 0x7E8, verify
```

Do not commit the VIN if the repository remains public. Calibration/strategy identifiers are enough for most protocol research.

## Best paths to the exact KOER command

### Path A, capture a known-good diagnostic session

Highest confidence. Capture the CAN traffic while FORScan, IDS or FCOM performs only the PCM KOER test on a matching platform. Diff:

1. idle diagnostic session;
2. KOER start;
3. KOER in progress;
4. KOER completion/result retrieval.

Look for new traffic involving the PCM physical diagnostic IDs and reconstruct ISO-TP payloads.

### Path B, exact Ford diagnostic documentation

Find documentation tied to the PCM calibration/strategy returned by `identify_pcm.py`. Record the service, subfunction/routine identifier, required diagnostic session and expected positive/negative responses.

### Path C, matching open-source implementation

A matching implementation is acceptable only if vehicle generation/protocol/PCM family line up. Record repository, commit and relevant code path here before using it live.

## Validation checklist before `koer.py`

- [ ] PCM calibration/strategy identified
- [ ] 0x7E0/0x7E8 physical addressing verified or corrected
- [ ] KOER request bytes supported by evidence
- [ ] any diagnostic-session transition understood
- [ ] any tester-present/keepalive behavior understood
- [ ] positive response format documented
- [ ] response-pending NRC handling documented if applicable
- [ ] completion/result query documented if separate from start
- [ ] engine-running and vehicle-stopped preconditions documented
- [ ] request reproduced first in dry-run output
- [ ] no PATS/security/write/flash service involved

## Candidate implementation shape

Once verified, add `scripts/koer.py` and keep the protocol constants explicit, for example:

```python
PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
KOER_START = bytes.fromhex("...")  # only after verified
```

If a diagnostic session is required, show each request separately in logs. Do not hide the traffic behind a large third-party abstraction.
