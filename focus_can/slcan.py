from __future__ import annotations

import glob
import os
import time
from dataclasses import dataclass
from typing import Optional

import serial


BITRATE_COMMANDS = {
    10000: "S0",
    20000: "S1",
    50000: "S2",
    100000: "S3",
    125000: "S4",
    250000: "S5",
    500000: "S6",
    800000: "S7",
    1000000: "S8",
}


@dataclass(frozen=True)
class CanFrame:
    arbitration_id: int
    data: bytes
    extended: bool = False
    remote: bool = False


def find_port() -> str:
    """Return CANABLE_PORT or the first likely macOS USB serial device."""
    configured = os.environ.get("CANABLE_PORT")
    if configured:
        return configured

    candidates: list[str] = []
    for pattern in (
        "/dev/cu.usbmodem*",
        "/dev/cu.usbserial*",
        "/dev/cu.SLAB_USBtoUART*",
        "/dev/cu.wchusbserial*",
    ):
        candidates.extend(sorted(glob.glob(pattern)))

    if not candidates:
        raise RuntimeError(
            "No CANable-like serial device found. Set CANABLE_PORT explicitly."
        )
    return candidates[0]


def parse_frame(line: bytes | str) -> Optional[CanFrame]:
    """Parse one Lawicel/SLCAN frame line. Return None for non-frame responses."""
    if isinstance(line, bytes):
        text = line.decode("ascii", errors="ignore")
    else:
        text = line

    text = text.strip("\r\n")
    if not text:
        return None

    kind = text[0]
    if kind not in "tTrR":
        return None

    extended = kind in "TR"
    remote = kind in "rR"
    id_chars = 8 if extended else 3

    if len(text) < 1 + id_chars + 1:
        return None

    arbitration_id = int(text[1 : 1 + id_chars], 16)
    dlc = int(text[1 + id_chars], 16)
    payload_text = text[2 + id_chars :]

    if remote:
        return CanFrame(arbitration_id, b"", extended=extended, remote=True)

    expected = dlc * 2
    if len(payload_text) < expected:
        return None

    data = bytes.fromhex(payload_text[:expected])
    return CanFrame(arbitration_id, data, extended=extended, remote=False)


def encode_frame(frame: CanFrame) -> bytes:
    if len(frame.data) > 8:
        raise ValueError("Classical CAN frame payload cannot exceed 8 bytes")

    if frame.extended:
        prefix = "R" if frame.remote else "T"
        identifier = f"{frame.arbitration_id:08X}"
    else:
        if frame.arbitration_id > 0x7FF:
            raise ValueError("11-bit CAN ID exceeds 0x7FF")
        prefix = "r" if frame.remote else "t"
        identifier = f"{frame.arbitration_id:03X}"

    dlc = len(frame.data)
    payload = "" if frame.remote else frame.data.hex().upper()
    return f"{prefix}{identifier}{dlc:X}{payload}\r".encode("ascii")


class Slcan:
    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 115200,
        timeout: float = 0.15,
    ) -> None:
        self.port = port or find_port()
        self.serial = serial.Serial(self.port, baudrate=baudrate, timeout=timeout)
        self._is_open = False

    def __enter__(self) -> "Slcan":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.shutdown()

    def _write(self, command: bytes) -> None:
        self.serial.write(command)
        self.serial.flush()

    def command(self, command: str, wait: float = 0.08) -> bytes:
        """Send an SLCAN command and return bytes currently waiting afterward."""
        self._write((command + "\r").encode("ascii"))
        time.sleep(wait)
        return self.serial.read(self.serial.in_waiting or 1)

    def close_channel(self) -> bytes:
        response = self.command("C")
        self._is_open = False
        return response

    def version(self) -> bytes:
        return self.command("V", wait=0.15)

    def open_channel(self, bitrate: int = 500000, silent: bool = False) -> None:
        if bitrate not in BITRATE_COMMANDS:
            raise ValueError(f"Unsupported SLCAN bitrate: {bitrate}")

        self.close_channel()
        self.serial.reset_input_buffer()

        response = self.command(BITRATE_COMMANDS[bitrate])
        self._raise_on_bell("setting bitrate", response)

        if silent:
            response = self.command("M1")
            self._raise_on_bell("enabling silent mode", response)

        response = self.command("O", wait=0.1)
        self._raise_on_bell("opening CAN channel", response)
        self._is_open = True

    @staticmethod
    def _raise_on_bell(action: str, response: bytes) -> None:
        if b"\x07" in response:
            raise RuntimeError(f"SLCAN adapter rejected command while {action}")

    def send(self, arbitration_id: int, data: bytes, extended: bool = False) -> None:
        if not self._is_open:
            raise RuntimeError("CAN channel is not open")
        self._write(encode_frame(CanFrame(arbitration_id, data, extended=extended)))

    def read_line(self, timeout: float | None = None) -> bytes:
        previous = self.serial.timeout
        if timeout is not None:
            self.serial.timeout = timeout
        try:
            return self.serial.read_until(b"\r")
        finally:
            self.serial.timeout = previous

    def read_frame(self, timeout: float = 1.0) -> Optional[CanFrame]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            raw = self.read_line(timeout=min(0.2, remaining))
            if not raw:
                continue
            frame = parse_frame(raw)
            if frame is not None:
                return frame
        return None

    def shutdown(self) -> None:
        try:
            if self.serial.is_open:
                try:
                    self.close_channel()
                finally:
                    self.serial.close()
        except Exception:
            # Shutdown should never hide the original exception.
            pass
