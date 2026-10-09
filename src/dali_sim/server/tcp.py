"""JSON-lines TCP API: lets any program (any language, any container) use
the simulated bus as if it were a DALI interface.

One JSON object per line in both directions.  Requests carry an optional
``id`` that the response repeats.

Requests::

    {"id": 1, "op": "hello"}
    {"id": 2, "op": "send", "frames": ["FF90"]}                 # 16-bit frame as hex
    {"id": 3, "op": "send", "frames": ["A303", "012E", "012E"]} # a transaction
    {"id": 4, "op": "send", "frames": ["C10100"], "expect_answer": false}
    {"id": 5, "op": "subscribe"}                                 # stream the bus monitor
    {"id": 6, "op": "snapshot"}
    {"id": 7, "op": "act", "action": "press", "device": "R1 switch", "instance": 0}

Responses::

    {"id": 2, "ok": true, "status": "answer", "value": 255, "responders": ["g0"]}
    {"id": 2, "ok": true, "status": "none"}
    {"id": 2, "ok": true, "status": "framing_error"}
    {"ok": false, "error": "..."}

After ``subscribe`` the server also sends ``{"event": "record", ...}`` lines
for every bus monitor record.  See docs/tcp-api.md.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from .. import __version__
from ..bus import BusRecord
from ..protocol.frames import Frame

if TYPE_CHECKING:  # pragma: no cover
    from ..sim import Simulator

PROTOCOL_VERSION = 1


def parse_frame(text: str) -> Frame:
    h = text.replace(" ", "").replace(":", "")
    if len(h) not in (2, 4, 6, 8) or any(c not in "0123456789abcdefABCDEF" for c in h):
        raise ValueError(f"bad frame {text!r}: give 4 (16-bit) or 6 (24-bit) hex digits")
    return Frame.from_bytes(bytes.fromhex(h))


class TcpServer:
    def __init__(self, sim: Simulator, host: str = "0.0.0.0", port: int = 5555) -> None:
        self.sim = sim
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self.clients = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.clients += 1
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10000)
        subscribed = False

        def on_record(rec: BusRecord) -> None:
            try:
                queue.put_nowait({"event": "record", **rec.to_dict()})
            except asyncio.QueueFull:
                pass

        async def pump() -> None:
            while True:
                msg = await queue.get()
                writer.write((json.dumps(msg) + "\n").encode())
                await writer.drain()

        pump_task = asyncio.create_task(pump())
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                    resp = await self._dispatch(req)
                    if req.get("op") == "subscribe" and not subscribed:
                        subscribed = True
                        self.sim.bus.listeners.append(on_record)
                except Exception as exc:  # report and keep the connection
                    req = req if isinstance(req, dict) else {}
                    resp = {"ok": False, "error": str(exc)}
                if isinstance(req, dict) and "id" in req:
                    resp["id"] = req["id"]
                await queue.put(resp)
        finally:
            if subscribed:
                try:
                    self.sim.bus.listeners.remove(on_record)
                except ValueError:
                    pass
            pump_task.cancel()
            writer.close()
            self.clients -= 1

    async def _dispatch(self, req: dict[str, Any]) -> dict[str, Any]:
        op = req.get("op")
        if op == "hello":
            return {"ok": True, "server": "dali-sim", "version": __version__, "protocol": PROTOCOL_VERSION,
                    "name": self.sim.name}
        if op == "send":
            frames = [parse_frame(f) for f in req.get("frames", [])]
            if not frames:
                raise ValueError("no frames")
            reply = await self.sim.bus.send(frames, source=str(req.get("source", "tcp")),
                                            priority=int(req.get("priority", 3)),
                                            expect_answer=req.get("expect_answer"))
            out = {"ok": True, "status": reply.status, "responders": reply.responders}
            if reply.value is not None:
                out["value"] = reply.value
            return out
        if op == "subscribe":
            return {"ok": True, "subscribed": True}
        if op == "snapshot":
            return {"ok": True, "snapshot": self.sim.snapshot()}
        if op == "act":
            args = {k: v for k, v in req.items() if k not in ("op", "id", "action")}
            return self.sim.act(req["action"], **args)
        raise ValueError(f"unknown op {op!r}")
