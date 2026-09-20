#!/usr/bin/env python3
"""Read a small, non-sensitive set of standardized UDS PCM identifiers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime

from focus_can.isotp import IsoTpClient
from focus_can.slcan import Slcan, find_port


PCM_TX_ID = 0x7E0
PCM_RX_ID = 0x7E8
READ_DATA_BY_IDENTIFIER = 0x22


@dataclass(frozen=True)
class DidDefinition:
    did: int
    meaning: str


# ISO 14229 standardized identification DIDs.  F190 (VIN) and ECU serial
# number identifiers are intentionally excluded from this public workflow.
DIDS = (
    DidDefinition(0xF180, "boot software identification"),
    DidDefinition(0xF181, "application software identification"),
    DidDefinition(0xF182, "application data identification"),
    DidDefinition(0xF187, "vehicle manufacturer spare part number"),
    DidDefinition(0xF188, "vehicle manufacturer ECU software number"),
    DidDefinition(0xF18A, "system supplier identifier"),
    DidDefinition(0xF18B, "ECU manufacturing date"),
)


def request_for(did: int) -> bytes:
    """Return the exact UDS ReadDataByIdentifier request for a DID."""
    if not 0 <= did <= 0xFFFF:
        raise ValueError("DID must fit in two bytes")
    return bytes((READ_DATA_BY_IDENTIFIER, did >> 8, did & 0xFF))


def printable_value(data: bytes) -> str:
    """Decode identification bytes conservatively for terminal output."""
    text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)
    return text.rstrip(". ") or "(no printable characters)"


def print_query_plan() -> None:
    print("Read-only PCM identification plan")
    print(f"CAN: 0x{PCM_TX_ID:X} -> 0x{PCM_RX_ID:X}, UDS default session unchanged")
    for definition in DIDS:
        request = request_for(definition.did)
        print(
            f"DID 0x{definition.did:04X}: {definition.meaning}; "
            f"TX {request.hex(' ').upper()}"
        )


def describe_response(definition: DidDefinition, response: bytes) -> None:
    print(f"\nDID 0x{definition.did:04X} ({definition.meaning})")
    print(f"RX 0x{PCM_RX_ID:X}: {response.hex(' ').upper()}")
    if len(response) >= 3 and response[:2] == bytes((0x7F, READ_DATA_BY_IDENTIFIER)):
        print(f"Unsupported/not readable in this session (NRC 0x{response[2]:02X}); continuing")
        return
    expected_prefix = bytes((READ_DATA_BY_IDENTIFIER + 0x40, definition.did >> 8, definition.did & 0xFF))
    if response[:3] != expected_prefix:
        print("Unexpected response shape; raw response retained")
        return
    value = response[3:]
    print(f"Value: {printable_value(value)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="transmit the read-only requests after printing the plan",
    )
    args = parser.parse_args()

    print_query_plan()
    if not args.execute:
        print("\nDRY RUN, nothing transmitted. Add --execute to perform these read-only queries.")
        return

    port = find_port()
    print(f"\nCANable: {port}")
    print(f"Time: {datetime.now().isoformat(timespec='seconds')}")
    with Slcan(port=port) as can:
        can.open_channel(500000, silent=False)
        client = IsoTpClient(can, tx_id=PCM_TX_ID, rx_id=PCM_RX_ID, timeout=3.0)
        for definition in DIDS:
            request = request_for(definition.did)
            print(f"\nTX 0x{PCM_TX_ID:X}: {request.hex(' ').upper()}")
            try:
                response = client.request(request)
            except TimeoutError as error:
                print(f"No response: {error}; continuing")
                continue
            describe_response(definition, response)


if __name__ == "__main__":
    main()
