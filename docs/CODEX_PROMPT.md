# Codex handoff prompt

Copy/paste the prompt below into Codex from the root of this repository.

```text
Read AGENTS.md, README.md, and docs/KOER_RESEARCH.md before changing anything.

You are continuing an existing macOS automotive diagnostics project for the owner's 2013 Ford Focus gasoline vehicle with a blade key. The goal is narrowly scoped: reproduce the normal PCM Key On Engine Running On Demand Self Test (KOER) state that FORScan uses before the owner manually clears MyKey from the instrument cluster.

Do not rewrite the project from scratch. Use the existing Python/SLCAN/ISO-TP implementation and existing scripts.

Verified live facts from the actual vehicle:
- host: macOS
- adapter: MKS CANable V2.0 Pro
- firmware observed: 16e7497-dirty
- HS-CAN works at 500 kbit/s
- passive capture succeeds
- PCM calibration: HFCR3PS.H32
- ECU name: ECM.-EngineControl
- PCM physical request ID: 0x7E0
- PCM physical response ID: 0x7E8
- Mode 09 identification succeeded over 0x7E0 -> 0x7E8
- a prior Mode 01 timeout was transient/state-related and does not invalidate the confirmed addressing
- do not store or commit the VIN because this repository is public

No KOER, PATS, SecurityAccess, configuration writes, ECU resets, As-Built changes, downloads, uploads, or programming operations have been performed.

Current Ford research strongly suggests a candidate local On-Demand Self-Test routine:
- start candidate: 31 02 00
- positive candidate: 71 02 00 ...
- result poll candidate: 33 02 00
- response pending candidate: 7F 33 78
- completed result candidate: 73 02 00 ...

Those bytes are research candidates only. Do not transmit them yet.

Your immediate task is to determine the diagnostic-session semantics used by PCM calibration HFCR3PS.H32, then use that result to validate the exact KOER request sequence for this PCM.

Start by inspecting and testing the repo:

git pull
bash scripts/setup.sh
source .venv/bin/activate
python -m unittest discover -s tests -v
python scripts/probe_canable.py
python scripts/probe_diag_session.py

Do not run active commands without first showing the exact bytes that will be transmitted and why they are safe/relevant.

For scripts/probe_diag_session.py:
- 10 01 is the first diagnostic-session probe
- if it returns 50 01, stop and treat UDS-style session control as confirmed
- only if it returns a diagnostic negative response to service 0x10 should 10 81 be tried
- if 10 81 returns 50 81, treat legacy Ford/KWP-style session semantics as strongly supported
- if either probe times out or returns something unexpected, stop rather than guessing
- do not append KOER execution to this script

After the live session-probe output is available:
1. record the exact TX/RX bytes in docs/KOER_RESEARCH.md;
2. search for evidence tied to HFCR3PS.H32, its PCM family, or same-generation Focus PCM diagnostics;
3. establish whether local routine 0x0202 maps to PCM On-Demand Self-Test on this PCM;
4. establish the exact required diagnostic session;
5. establish whether start/result services are really 0x31 / 0x33 on this PCM;
6. establish response-pending and completion behavior;
7. establish any tester-present requirement;
8. establish KOER preconditions, especially engine-running / vehicle-stopped requirements.

Prefer evidence in this order:
1. CAN trace from FORScan/IDS/FCOM performing PCM KOER on a same-generation Focus;
2. Ford diagnostic documentation for the matching PCM/calibration family;
3. matching Ford diagnostic definition data;
4. reputable open-source implementation with matching protocol/platform;
5. controlled live interrogation that is non-persistent and does not mutate configuration/security state.

Do not infer Focus behavior solely from FG Falcon or unrelated Ford modules. They are supporting evidence, not proof.

Only after the session model and KOER routine are sufficiently validated should you implement scripts/koer.py.

Requirements for scripts/koer.py:
- default to dry-run
- require --execute for live transmission
- use 0x7E0 -> 0x7E8
- show every TX/RX payload
- use the exact evidence-backed session sequence
- handle ISO-TP correctly
- explicitly handle negative responses and response-pending status
- use tester-present only if required by evidence
- timeout cleanly
- Ctrl-C must leave the CAN channel cleanly closed
- never request SecurityAccess
- never touch PATS
- never write configuration or As-Built data
- never reset or reflash an ECU
- never erase/relearn keys
- never send an alternate routine because the expected one failed

When KOER completes successfully, stop active diagnostic traffic and display manual instructions only:
1. keep the driver's door closed;
2. wrap the plastic head of the blade key in foil;
3. turn ignition OFF;
4. quickly return ignition to ON/RUN without cranking;
5. use the cluster Settings -> MyKey -> Clear All function;
6. hold OK until the cluster confirms the clear.

Keep the implementation small and auditable. Add unit tests for any new ISO-TP/session/response parsing logic. Update docs/KOER_RESEARCH.md with evidence and clearly distinguish VERIFIED facts from CANDIDATE assumptions.

Do not commit the VIN.
```
