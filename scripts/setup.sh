#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

"$PYTHON" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p logs

cat <<'EOF'

Setup complete.

Next:
  source .venv/bin/activate
  bash scripts/detect_canable.sh
  python scripts/probe_canable.py
EOF
