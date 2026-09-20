#!/usr/bin/env bash
set -euo pipefail

printf 'Likely CANable serial devices on macOS:\n\n'
found=0
for pattern in /dev/cu.usbmodem* /dev/cu.usbserial* /dev/cu.SLAB_USBtoUART* /dev/cu.wchusbserial*; do
  for dev in $pattern; do
    if [[ -e "$dev" ]]; then
      printf '  %s\n' "$dev"
      found=1
    fi
  done
done

if [[ "$found" -eq 0 ]]; then
  echo 'No likely USB serial CAN adapter found.'
  exit 1
fi

cat <<'EOF'

If more than one device is listed, choose the CANable explicitly:

  export CANABLE_PORT=/dev/cu.usbmodemXXXXXXXX

The port observed previously for this board was:

  /dev/cu.usbmodem207E38A238461
EOF
