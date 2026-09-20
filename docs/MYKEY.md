# MyKey clear target procedure

## What this project is trying to reproduce

FORScan documents a workaround for a vehicle where the only available key is a MyKey and there is no admin key. The relevant state change comes from running the PCM **Key On Engine Running On Demand Self Test (KOER)**, then cycling the ignition while the key transponder is not detected.

Reference:

- FORScan forum, "How to clear MyKey without admin key and programming": https://forum.forscan.org/viewtopic.php?t=11739

That thread also contains a report from a **2013 Ford Focus** owner who initially failed and later reported success after retrying the procedure.

## Intended sequence

1. Start the engine normally with the remaining blade key.
2. Connect the diagnostic tool to the PCM.
3. Run PCM **KOER on-demand self-test** and wait for it to complete.
4. Keep the driver's door closed.
5. Wrap the **plastic head/transponder area** of the blade key tightly in foil.
6. Turn ignition completely OFF.
7. Quickly turn ignition back to ON/RUN without cranking the engine.
8. In the instrument cluster, navigate to `Settings -> MyKey -> Clear All`.
9. Hold OK until the cluster confirms the MyKeys were cleared.
10. Remove the foil and cycle the ignition normally.

A later community report emphasizes not opening the driver's door and doing the OFF -> ON cycle quickly, within a few seconds:

- https://www.f150forum.com/f118/my-key-550653/

## Important limitation

The workaround is documented at the **FORScan function level**, not as a raw CAN payload. This repository must not invent a KOER CAN/UDS request. The exact request sequence for this generation Focus PCM needs to be identified and verified before `scripts/koer.py` is implemented.

## What we are deliberately not doing

- no PATS erase/relearn;
- no immobilizer bypass;
- no ECU/module firmware flashing;
- no guessed IPC As-Built writes;
- no generic "disable MyKey" configuration change that could leave some restrictions active.

The goal is to make the cluster itself perform the normal **Clear All MyKeys** operation once the vehicle is in the appropriate diagnostic state.
