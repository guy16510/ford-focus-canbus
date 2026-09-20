from __future__ import annotations

import time

from .slcan import CanFrame, Slcan


class IsoTpError(RuntimeError):
    pass


class IsoTpClient:
    """Minimal ISO-TP client for one physical 11-bit CAN request/response pair."""

    def __init__(
        self,
        bus: Slcan,
        tx_id: int = 0x7E0,
        rx_id: int = 0x7E8,
        timeout: float = 2.0,
    ) -> None:
        self.bus = bus
        self.tx_id = tx_id
        self.rx_id = rx_id
        self.timeout = timeout

    def _wait_for_rx(self, timeout: float | None = None) -> CanFrame:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while time.monotonic() < deadline:
            frame = self.bus.read_frame(timeout=min(0.25, deadline - time.monotonic()))
            if frame is not None and frame.arbitration_id == self.rx_id:
                return frame
        raise TimeoutError(f"Timed out waiting for CAN ID 0x{self.rx_id:X}")

    @staticmethod
    def _stmin_seconds(raw: int) -> float:
        if raw <= 0x7F:
            return raw / 1000.0
        if 0xF1 <= raw <= 0xF9:
            return (raw - 0xF0) / 10000.0
        return 0.0

    def send_payload(self, payload: bytes) -> None:
        if not payload:
            raise ValueError("ISO-TP payload cannot be empty")
        if len(payload) > 0xFFF:
            raise ValueError("This helper supports ISO-TP payloads up to 4095 bytes")

        if len(payload) <= 7:
            frame = bytes([len(payload)]) + payload
            self.bus.send(self.tx_id, frame.ljust(8, b"\x00"))
            return

        total = len(payload)
        first = bytes([0x10 | ((total >> 8) & 0x0F), total & 0xFF]) + payload[:6]
        self.bus.send(self.tx_id, first.ljust(8, b"\x00"))

        fc = self._wait_for_rx()
        if len(fc.data) < 3 or (fc.data[0] >> 4) != 0x3:
            raise IsoTpError(f"Expected Flow Control, got {fc.data.hex(' ')}")

        flow_status = fc.data[0] & 0x0F
        if flow_status != 0:
            raise IsoTpError(f"ECU returned ISO-TP flow status {flow_status}")

        block_size = fc.data[1]
        stmin = self._stmin_seconds(fc.data[2])
        offset = 6
        sequence = 1
        in_block = 0

        while offset < total:
            chunk = payload[offset : offset + 7]
            cf = bytes([0x20 | (sequence & 0x0F)]) + chunk
            self.bus.send(self.tx_id, cf.ljust(8, b"\x00"))
            offset += len(chunk)
            sequence = (sequence + 1) & 0x0F
            in_block += 1
            if stmin:
                time.sleep(stmin)

            if block_size and in_block >= block_size and offset < total:
                fc = self._wait_for_rx()
                if len(fc.data) < 3 or (fc.data[0] >> 4) != 0x3:
                    raise IsoTpError("Expected another Flow Control frame")
                if (fc.data[0] & 0x0F) != 0:
                    raise IsoTpError("ECU paused/rejected continued ISO-TP transfer")
                block_size = fc.data[1]
                stmin = self._stmin_seconds(fc.data[2])
                in_block = 0

    def receive_payload(self) -> bytes:
        first = self._wait_for_rx()
        if not first.data:
            raise IsoTpError("Received empty CAN frame")

        frame_type = first.data[0] >> 4

        if frame_type == 0x0:
            length = first.data[0] & 0x0F
            return first.data[1 : 1 + length]

        if frame_type != 0x1 or len(first.data) < 2:
            raise IsoTpError(f"Unexpected ISO-TP PCI: {first.data.hex(' ')}")

        total = ((first.data[0] & 0x0F) << 8) | first.data[1]
        assembled = bytearray(first.data[2:])

        # Continue-to-send, no block limit, no requested separation delay.
        self.bus.send(self.tx_id, bytes([0x30, 0x00, 0x00]).ljust(8, b"\x00"))

        expected_sequence = 1
        while len(assembled) < total:
            frame = self._wait_for_rx()
            if not frame.data or (frame.data[0] >> 4) != 0x2:
                raise IsoTpError(f"Expected Consecutive Frame, got {frame.data.hex(' ')}")
            sequence = frame.data[0] & 0x0F
            if sequence != expected_sequence:
                raise IsoTpError(
                    f"ISO-TP sequence mismatch: expected {expected_sequence:X}, got {sequence:X}"
                )
            assembled.extend(frame.data[1:])
            expected_sequence = (expected_sequence + 1) & 0x0F

        return bytes(assembled[:total])

    def request(self, payload: bytes) -> bytes:
        self.send_payload(payload)
        return self.receive_payload()
