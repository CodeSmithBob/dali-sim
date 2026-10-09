"""AtxLED DALI HAT for Raspberry Pi (serial, 19200 8N1, ASCII lines).

Host to HAT: a prefix letter for the frame size, the frame in hex, newline::

    h0190      16-bit frame          l010203   24-bit frame
    t0120      16-bit frame, sent twice by the HAT
    j5A        8-bit frame

HAT to host, one line per transmission::

    Jhh   answer received          N   no answer
    X     transmission failed      Z   collision (send again)

Facts from open-source drivers (see docs/sources.md).  NOT YET TESTED ON
HARDWARE by the dali-sim authors.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities
from .serial_line import LineTransport

PREFIX = {8: "j", 16: "h", 24: "l"}


def request_line(frame: Frame, twice: bool) -> str:
    if twice and frame.bits == 16:
        return "t" + frame.to_bytes().hex().upper()
    return PREFIX[frame.bits] + frame.to_bytes().hex().upper()


def parse_line(line: str) -> Reply | str | None:
    """A Reply for answer lines, "retry"/"fail" for Z/X, None for others."""
    if not line:
        return None
    c = line[0]
    if c == "J":
        try:
            return Reply("answer", int(line[1:3], 16))
        except ValueError:
            return Reply("framing_error")
    if c == "N":
        return NO_ANSWER
    if c == "Z":
        return "retry"
    if c == "X":
        return "fail"
    return None


class AtxLedAdapter(Adapter):
    name = "atxled"
    caps = Capabilities(forward24=True, sniff=False, send_backward=False, native_twice=True,
                        notes="AtxLED DALI HAT (serial); protocol from public drivers, needs hardware testing")

    def __init__(self, path: str = "/dev/serial0", baudrate: int = 19200, **options: Any) -> None:
        super().__init__(**options)
        self.io = LineTransport(path, baudrate)
        self._lines: asyncio.Queue[str] = asyncio.Queue()

    async def _open(self) -> None:
        self.io.on_line = self._lines.put_nowait
        self.io.open(asyncio.get_running_loop())

    async def _close(self) -> None:
        self.io.close()

    async def _result(self) -> Reply | str:
        while True:
            try:
                line = await asyncio.wait_for(self._lines.get(), 1.0)
            except asyncio.TimeoutError:
                raise AdapterError(f"{self.name}: no response from the HAT") from None
            r = parse_line(line)
            if r is not None:
                return r

    async def _once(self, line: str, results: int) -> Reply:
        for _attempt in range(5):
            while not self._lines.empty():
                self._lines.get_nowait()
            self.io.write(line)
            got: Reply | str = NO_ANSWER
            for _ in range(results):
                got = await self._result()
                if isinstance(got, str):
                    break
            if got == "retry":
                await asyncio.sleep(0.05)
                continue
            if got == "fail":
                raise AdapterError(f"{self.name}: transmission failed")
            return got  # type: ignore[return-value]
        raise AdapterError(f"{self.name}: collisions, gave up after 5 attempts")

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        if twice and frame.bits != 16:
            await self._once(request_line(frame, False), 1)
            return await self._once(request_line(frame, False), 1)
        return await self._once(request_line(frame, twice), 2 if twice else 1)
