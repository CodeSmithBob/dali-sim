"""hasseb DALI Master (USB HID, vendor 0x04CC, product 0x0802).

Protocol of the current firmware as used by open-source drivers (see
docs/sources.md).  Reports are 10 bytes in both directions.

Host to interface::

    0xAA, command, sequence, bits, expect_answer, settling_time, send_twice_ms, byte1, byte2, 0

    command 0x02  read firmware version
            0x05  configure (bits field = 1 enables the sniffer)
            0x07  send a DALI frame

Interface to host::

    report_id, command, sequence, status, data_length, data, ...

    status for command 0x07:
      0 no data available (sent continuously while idle)
      1 no answer
      2 answer (data_length 1, data = the backward frame)
      3 invalid answer (framing error)
      4 answer too early
      5 sniffer byte, 6 sniffer byte error

The interface sends frames twice itself (send_twice_ms = gap) and only
sends 16-bit frames in this driver.  It cannot transmit backward frames, so
virtual devices cannot answer a real controller through it.

NOT YET TESTED ON HARDWARE by the dali-sim authors: please send a hardware
report.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities
from .hid_transport import HidTransport

VID, PID = 0x04CC, 0x0802
REPORT_ID = 0xAA
CMD_FIRMWARE, CMD_CONFIGURE, CMD_FRAME = 0x02, 0x05, 0x07
NO_DATA, NO_ANSWER_ST, OK, INVALID, TOO_EARLY, SNIFF, SNIFF_ERR = range(7)


def build_request(seq: int, frame: Frame, expect_answer: bool, twice: bool) -> bytes:
    if frame.bits != 16:
        raise AdapterError("hasseb: only 16-bit frames are supported")
    b1, b2 = frame.to_bytes()
    return bytes([REPORT_ID, CMD_FRAME, seq & 0xFF, 16, int(expect_answer), 0, 10 if twice else 0, b1, b2, 0])


def parse_report(data: bytes) -> tuple[int, int, int, int | None] | None:
    """(command, sequence, status, value) or None for idle/unknown reports."""
    if len(data) < 4:
        return None
    cmd, seq, status = data[1], data[2], data[3]
    if cmd == CMD_FRAME and status == NO_DATA:
        return None
    value = data[5] if len(data) > 5 and status == OK and data[4] >= 1 else None
    return cmd, seq, status, value


class HassebAdapter(Adapter):
    name = "hasseb"
    caps = Capabilities(forward24=False, sniff=False, send_backward=False, native_twice=True,
                        notes="hasseb DALI Master; 16-bit frames; protocol from public drivers, needs hardware testing")

    def __init__(self, path: str | None = None, transport: Any = None, **options: Any) -> None:
        super().__init__(**options)
        self.io = transport or HidTransport(path, VID, PID, report_size=10)
        self._seq = 0
        self._reports: asyncio.Queue[tuple[int, int, int, int | None]] = asyncio.Queue()
        self.firmware = ""

    def _next_seq(self) -> int:
        self._seq = self._seq % 255 + 1
        return self._seq

    async def _open(self) -> None:
        self.io.on_report = self._report
        self.io.open(asyncio.get_running_loop())
        seq = self._next_seq()
        self.io.write(bytes([REPORT_ID, CMD_FIRMWARE, seq, 0, 0, 0, 0, 0, 0, 0]))
        try:
            rep = await self._wait(CMD_FIRMWARE, 1.0)
            self.firmware = f"{rep[2]}.{rep[3] if rep[3] is not None else 0}"
        except AdapterError:
            self.firmware = "unknown"

    async def _close(self) -> None:
        self.io.close()

    def _report(self, data: bytes) -> None:
        parsed = parse_report(data)
        if parsed is None:
            return
        if parsed[0] == CMD_FIRMWARE:
            # firmware answer: major in byte 3, minor in byte 4
            self._reports.put_nowait((CMD_FIRMWARE, parsed[1], data[3], data[4] if len(data) > 4 else 0))
            return
        self._reports.put_nowait(parsed)

    async def _wait(self, cmd: int, timeout: float) -> tuple[int, int, int, int | None]:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            left = end - loop.time()
            if left <= 0:
                raise AdapterError(f"{self.name}: no response from the interface")
            try:
                rep = await asyncio.wait_for(self._reports.get(), left)
            except asyncio.TimeoutError:
                raise AdapterError(f"{self.name}: no response from the interface") from None
            if rep[0] == cmd and rep[2] not in (SNIFF, SNIFF_ERR):
                return rep

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        while not self._reports.empty():
            self._reports.get_nowait()
        self.io.write(build_request(self._next_seq(), frame, expect_answer, twice))
        if not expect_answer:
            # 19 bit times per frame, plus settling; the interface does not confirm.
            await asyncio.sleep(0.04 if twice else 0.02)
            return NO_ANSWER
        _, _, status, value = await self._wait(CMD_FRAME, 1.0)
        if status == OK and value is not None:
            return Reply("answer", value)
        if status in (INVALID, TOO_EARLY):
            return Reply("framing_error")
        return NO_ANSWER
