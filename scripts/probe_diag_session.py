#!/usr/bin/env python3
from __future__ import annotations

import argparse

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port

PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8


def fmt(data: bytes) -> str:
    return data.hex(" ").upper()


def request(client: IsoTpClient, payload: bytes, label: str) -> bytes | None:
    print(f"TX {PCM_TX_ID:#05x}  {fmt(payload):<20} {label}")
    try:
        response = client.request(payload)
    except TimeoutError:
        print(f"RX {PCM_RX_ID:#05x}  <timeout>")
        return None

    print(f"RX {PCM_RX_ID:#05x}  {fmt(response)}")
    return response


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Guarded, non-persistent probe to distinguish UDS-style versus "
            "legacy Ford/KWP-style PCM diagnostic-session semantics."
        )
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually transmit diagnostic-session requests; default is dry-run",
    )
    args = parser.parse_args()

    print("PCM diagnostic-session compatibility probe")
    print(f"Physical addressing: {PCM_TX_ID:#05x} -> {PCM_RX_ID:#05x}")
    print("No PATS, SecurityAccess, writes, resets, programming, or KOER requests are sent.\n")

    print("Plan:")
    print("  1. Send 10 01 (UDS/default-session form).")
    print("  2. If it positively answers 50 01, stop: UDS-style session control is confirmed.")
    print("  3. Only if 10 01 returns a negative diagnostic response, try 10 81.")
    print("     A positive 50 81 strongly supports legacy Ford/KWP-style session semantics.")
    print("  4. If 10 01 simply times out, stop rather than guessing.\n")

    if not args.execute:
        print("DRY RUN ONLY. Re-run with --execute to transmit the two-byte session probe.")
        return

    port = find_port()
    print(f"CANable: {port}")
    print("Ignition should be ON, engine does not need to be running for this probe.\n")

    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=2.5)

        uds = request(client, bytes.fromhex("10 01"), "DiagnosticSessionControl/default")
        if uds is None:
            print("\nStopped after timeout. Do not infer protocol from silence.")
            return

        if len(uds) >= 2 and uds[0:2] == bytes.fromhex("50 01"):
            print("\nResult: UDS-style default diagnostic session positively acknowledged.")
            print("Do not send the legacy 31 02 00 KOER candidate yet; its service layout may not apply.")
            return

        if len(uds) >= 3 and uds[0] == 0x7F and uds[1] == 0x10:
            print(
                f"\n10 01 rejected with NRC 0x{uds[2]:02X}; "
                "testing legacy Ford/KWP-style 10 81 next."
            )
        else:
            print("\nUnexpected response to 10 01. Stopping rather than trying another session form.")
            return

        kwp = request(client, bytes.fromhex("10 81"), "Ford/KWP standard diagnostic session")
        if kwp is None:
            print("\nNo response to 10 81. Protocol remains unresolved; do not proceed to KOER.")
            return

        if len(kwp) >= 2 and kwp[0:2] == bytes.fromhex("50 81"):
            print("\nResult: legacy Ford/KWP-style diagnostic-session semantics strongly supported.")
            print("Next research step is to validate local routine 0x0202 for this PCM before KOER execution.")
            return

        if len(kwp) >= 3 and kwp[0] == 0x7F:
            print(
                f"\n10 81 negative response: service=0x{kwp[1]:02X}, NRC=0x{kwp[2]:02X}. "
                "Do not proceed to KOER."
            )
            return

        print("\nUnexpected 10 81 response. Save the raw output and stop here.")


if __name__ == "__main__":
    main()
