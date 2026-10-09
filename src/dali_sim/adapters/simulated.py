"""Adapters that do not need hardware.

:class:`LoopbackAdapter` puts a second simulated bus behind the adapter
interface.  It can do everything (24-bit frames, sniffing, backward frames),
so it is used to test bridges and to model "the real bus" in demos.

:class:`RemoteAdapter` talks to another dali-sim over the JSON-lines TCP API,
e.g. a simulator running in a Docker container or on another machine.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from ..bus import Bus, BusRecord, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities, Observed


class LoopbackAdapter(Adapter):
    name = "loopback"
    caps = Capabilities(forward24=True, sniff=True, send_backward=True, timestamps=True,
                        notes="a second simulated bus behind the adapter API")

    def __init__(self, bus: Bus | None = None, **options: Any) -> None:
        super().__init__(**options)
        self.bus = bus or Bus()
        self._pending_backward: int | None = None

    async def _open(self) -> None:
        self.bus.frame_listeners.append(self._on_frame)

    async def _close(self) -> None:
        try:
            self.bus.frame_listeners.remove(self._on_frame)
        except ValueError:
            pass

    def _on_frame(self, frame: Frame, t: float, source: str, reply: Reply | None) -> None:
        if source == self.name:
            return
        self.emit(Observed(frame, "forward"))
        if reply is not None and reply.answered:
            self.emit(Observed(Frame.backward(reply.value), "backward"))

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        return await self.bus.send(frame, source=self.name, expect_answer=expect_answer)

    async def _send_backward(self, value: int) -> None:
        self.bus.record(BusRecord(self.bus.now(), "backward", f"answer 0x{value:02X} from {self.name}",
                                  Frame.backward(value), self.name))


class RemoteAdapter(Adapter):
    name = "remote"
    caps = Capabilities(forward24=True, sniff=True, send_backward=False,
                        notes="another dali-sim over the TCP API")

    def __init__(self, host: str = "127.0.0.1", port: int = 5555, **options: Any) -> None:
        super().__init__(**options)
        self.host = host
        self.port = int(port)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._ids = 0
        self._waiting: dict[int, asyncio.Future] = {}
        self._task: asyncio.Task | None = None

    async def _open(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self._task = asyncio.create_task(self._read_loop())
        hello = await self._call({"op": "hello"})
        if hello.get("server") != "dali-sim":
            raise AdapterError("peer is not a dali-sim server")
        await self._call({"op": "subscribe"})

    async def _close(self) -> None:
        if self._task:
            self._task.cancel()
        if self._writer:
            self._writer.close()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            line = await self._reader.readline()
            if not line:
                break
            msg = json.loads(line)
            if "id" in msg and msg["id"] in self._waiting:
                self._waiting.pop(msg["id"]).set_result(msg)
            elif msg.get("event") == "record" and msg.get("source") != "remote-adapter":
                kind = msg.get("kind")
                if kind in ("forward", "event") and msg.get("hex"):
                    self.emit(Observed(Frame.from_bytes(bytes.fromhex(msg["hex"].replace(" ", ""))), "forward"))
        for fut in self._waiting.values():
            fut.set_exception(AdapterError("connection closed"))

    async def _call(self, req: dict[str, Any]) -> dict[str, Any]:
        assert self._writer is not None
        self._ids += 1
        req["id"] = self._ids
        fut = asyncio.get_running_loop().create_future()
        self._waiting[self._ids] = fut
        self._writer.write((json.dumps(req) + "\n").encode())
        await self._writer.drain()
        resp = await asyncio.wait_for(fut, timeout=10)
        if not resp.get("ok"):
            raise AdapterError(resp.get("error", "error"))
        return resp

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        resp = await self._call({"op": "send", "frames": [frame.to_bytes().hex()], "expect_answer": expect_answer,
                                 "source": "remote-adapter"})
        return Reply(resp["status"], resp.get("value"), resp.get("responders", []))
