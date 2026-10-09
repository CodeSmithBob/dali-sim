"""A daliserver-compatible TCP server (protocol of github.com/onitake/daliserver,
port 55825), so tools written for daliserver, e.g. python-dali's
``DaliServer`` driver, can use the simulator unchanged."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..protocol.frames import Frame

if TYPE_CHECKING:  # pragma: no cover
    from ..sim import Simulator


class DaliServerProtocolServer:
    def __init__(self, sim: Simulator, host: str = "127.0.0.1", port: int = 55825) -> None:
        self.sim = sim
        self.host, self.port = host, port
        self._server: asyncio.base_events.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    req = await reader.readexactly(4)
                except asyncio.IncompleteReadError:
                    break
                if req[0] != 2 or req[1] != 0:
                    writer.write(bytes([2, 254, 0, 0]))
                    await writer.drain()
                    continue
                r = await self.sim.bus.send(Frame.forward16(req[2], req[3]), source="daliserver")
                status = 1 if r.answered else 255 if r.collision else 0
                writer.write(bytes([2, status, r.value or 0, 0]))
                await writer.drain()
        finally:
            writer.close()
