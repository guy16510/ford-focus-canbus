# AGENT.md

The canonical agent instructions are in [AGENTS.md](AGENTS.md).

Start here:

```bash
bash scripts/setup.sh
source .venv/bin/activate
bash scripts/detect_canable.sh
python -m unittest discover -s tests -v
python scripts/probe_canable.py
python scripts/listen_hscan.py
```

After passive HS-CAN traffic is confirmed:

```bash
python scripts/pcm_obd_probe.py
python scripts/identify_pcm.py
```

Then follow the KOER research/implementation tasks in `AGENTS.md`.
