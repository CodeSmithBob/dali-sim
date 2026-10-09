"""Client for the daliserver TCP protocol (github.com/onitake/daliserver,
default port 55825).  dali-sim also *serves* this protocol, so tools built for
daliserver can use the simulator (see server/daliserver.py).

Request (4 bytes): version 2, type 0, address byte, opcode byte.
Response (4 bytes): version 2, status (0 no answer, 1 answer, 255 framing
error), value, 0.  16-bit frames only; send-twice is two requests.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities


def request(frame: Frame) -> bytes:
    if frame.bits != 16:
        raise AdapterError("daliserver only carries 16-bit frames")
    return bytes([2, 0]) + frame.to_bytes()


def parse_response(data: bytes) -> Reply:
    if len(data) != 4 or data[0] != 2:
        raise AdapterError(f"bad daliserver response {data.hex()}")
    status, value = data[1], data[2]
    if status == 1:
        return Reply("answer", value)
    if status == 255:
        return Reply("framing_error")
    if status == 0:
        return NO_ANSWER
    raise AdapterError(f"daliserver status {status}")


class DaliServerAdapter(Adapter):
    name = "daliserver"
    caps = Capabilities(forward24=False, sniff=False, send_backward=False, notes="daliserver TCP protocol")

    def __init__(self, host: str = "127.0.0.1", port: int = 55825, **options: Any) -> None:
        super().__init__(**options)
        self.host, self.port = host, int(port)
        self._r: asyncio.StreamReader | None = None
        self._w: asyncio.StreamWriter | None = None

    async def _open(self) -> None:
        self._r, self._w = await asyncio.open_connection(self.host, self.port)

    async def _close(self) -> None:
        if self._w:
            self._w.close()

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        assert self._w and self._r
        self._w.write(request(frame))
        await self._w.drain()
        return parse_response(await asyncio.wait_for(self._r.readexactly(4), 2.0))
