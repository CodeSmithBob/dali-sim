"""Tridonic DALI USB, and the older Lunatone DALI USB with the same USB IDs
(USB HID, vendor 0x17B5, product 0x0020).

Protocol as used by open-source drivers (see docs/sources.md).  Reports are
64 bytes.

Host to interface::

    command, sequence, control, mode, frame[4], dtr, priority, device_type, 0 * 53

    command 0x01 init (sequence byte 0x00 = read version, 0x02 = read serial)
            0x12 send a frame          0x40 bus power supply (Lunatone variants)
    control bit 0x20  send twice
    mode    2 = 8-bit, 3 = 16-bit, 6 = 24-bit; the frame is right-aligned in 4 bytes

Interface to host::

    origin, type, frame[4], interval(2), sequence, 0 * 55

    origin 0x01 init answer, 0x11 traffic observed on the bus, 0x12 answer to our send
    type   0x71 no answer, 0x72 8-bit frame, 0x73 16-bit frame, 0x76 24-bit frame,
           0x77 bus status in frame[3] (3 = framing error, 2 = bus shorted)

After a send, the interface echoes each transmitted frame (type 0x73/0x76),
then reports the answer (0x72), no answer (0x71) or a framing error (0x77).
Observed traffic from other controllers arrives with origin 0x11, so the
interface can sniff the bus.

NOT YET TESTED ON HARDWARE by the dali-sim authors: please send a hardware
report.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities, Observed
from .hid_transport import HidTransport

VID, PID = 0x17B5, 0x0020
CMD_INIT, CMD_SEND, CMD_POWER = 0x01, 0x12, 0x40
CTRL_TWICE = 0x20
MODE = {8: 2, 16: 3, 24: 6}
ORIGIN_INFO, ORIGIN_OBSERVE, ORIGIN_RESPONSE = 0x01, 0x11, 0x12
T_NONE, T_8, T_16, T_24, T_INFO = 0x71, 0x72, 0x73, 0x76, 0x77
BUS_SHORTED, BUS_FRAMING_ERROR = 2, 3


def build_send(seq: int, frame: Frame, twice: bool) -> bytes:
    if frame.bits not in (16, 24):
        raise AdapterError("only 16- and 24-bit frames can be sent")
    body = bytes([CMD_SEND, seq, CTRL_TWICE if twice else 0, MODE[frame.bits]]) + frame.value.to_bytes(4, "big")
    return body + bytes(64 - len(body))


def parse_report(data: bytes) -> tuple[int, int, bytes, int] | None:
    """(origin, type, frame[4], sequence)."""
    if len(data) < 9:
        return None
    return data[0], data[1], bytes(data[2:6]), data[8]


class TridonicAdapter(Adapter):
    name = "tridonic"
    caps = Capabilities(forward24=True, sniff=True, send_backward=False, native_twice=True,
                        notes="Tridonic / Lunatone DALI USB (17B5:0020); protocol from public drivers, needs hardware testing")

    def __init__(self, path: str | None = None, transport: Any = None, **options: Any) -> None:
        super().__init__(**options)
        self.io = transport or HidTransport(path, VID, PID, report_size=64)
        self._seq = 0
        self._pending: dict[int, asyncio.Queue] = {}
        self._init: asyncio.Queue[bytes] = asyncio.Queue()
        self.firmware = ""
        self.serial = ""

    def _next_seq(self) -> int:
        self._seq = self._seq % 255 + 1
        return self._seq

    async def _open(self) -> None:
        self.io.on_report = self._report
        self.io.open(asyncio.get_running_loop())
        try:
            self.io.write(bytes([CMD_INIT, 0x00]) + bytes(62))
            v = await asyncio.wait_for(self._init.get(), 1.0)
            self.firmware = f"{v[3]}.{v[4]}"
            self.io.write(bytes([CMD_INIT, 0x02]) + bytes(62))
            s = await asyncio.wait_for(self._init.get(), 1.0)
            self.serial = bytes(s[1:5]).hex().upper()
        except asyncio.TimeoutError:
            raise AdapterError(f"{self.name}: the interface did not answer the init command") from None

    async def _close(self) -> None:
        self.io.close()

    def _report(self, data: bytes) -> None:
        parsed = parse_report(data)
        if parsed is None:
            return
        origin, typ, raw, seq = parsed
        if origin == ORIGIN_INFO:
            self._init.put_nowait(data)
            return
        if origin == ORIGIN_RESPONSE and seq in self._pending:
            self._pending[seq].put_nowait((typ, raw))
            return
        if origin in (ORIGIN_OBSERVE, ORIGIN_RESPONSE):
            # Traffic of other transmitters.  (The interface may also report a
            # frame of someone else that equals its own last frame as a
            # response with an old sequence number; that lands here too.)
            if typ == T_16:
                self.emit(Observed(Frame(16, int.from_bytes(raw, "big") & 0xFFFF), "forward"))
            elif typ == T_24:
                self.emit(Observed(Frame(24, int.from_bytes(raw, "big") & 0xFFFFFF), "forward"))
            elif typ == T_8:
                self.emit(Observed(Frame.backward(raw[3]), "backward"))
            elif typ == T_INFO and raw[3] == BUS_FRAMING_ERROR:
                self.emit(Observed(None, "framing_error"))

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        seq = self._next_seq()
        q: asyncio.Queue = asyncio.Queue()
        self._pending[seq] = q
        try:
            self.io.write(build_send(seq, frame, twice))
            echoes = 2 if twice else 1
            reply: Reply | None = None
            while echoes or reply is None:
                try:
                    typ, raw = await asyncio.wait_for(q.get(), 0.25)
                except asyncio.TimeoutError:
                    if echoes:
                        raise AdapterError(f"{self.name}: frame not confirmed by the interface") from None
                    return NO_ANSWER
                if typ in (T_16, T_24):
                    echoes -= 1
                elif typ == T_8:
                    reply = Reply("answer", raw[3])
                elif typ == T_NONE:
                    reply = NO_ANSWER
                elif typ == T_INFO and raw[3] == BUS_FRAMING_ERROR:
                    reply = Reply("framing_error")
                elif typ == T_INFO and raw[3] == BUS_SHORTED:
                    raise AdapterError(f"{self.name}: bus shorted or without power")
            return reply
        finally:
            del self._pending[seq]
