"""Bridges between the simulated bus and a real one (through an adapter).

Three modes:

``controller``
    The simulator (web console, commissioning, test benches) drives *real*
    devices.  Frames go out through the adapter; answers come back.  Traffic
    shows up in the bus monitor.

``devices``
    A *real* controller drives *virtual* devices.  The adapter sniffs forward
    frames on the real bus; they are delivered to the simulated bus; if a
    virtual unit answers and the adapter can transmit backward frames, the
    answer goes back onto the real bus.

``hybrid``
    Virtual and real devices behave as one bus.  Frames from the simulator's
    controllers go to both; answers are merged as on a wired-AND bus (two
    different answers make a framing error).  Events of real control devices
    are fed into the simulated bus so virtual application controllers see
    them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .adapters.base import Adapter, Observed
from .bus import BusRecord, Reply
from .protocol.decode import FrameDecoder
from .protocol.frames import Frame, resolve_collision

if TYPE_CHECKING:  # pragma: no cover
    from .sim import Simulator

MODES = ("controller", "devices", "hybrid")


class Bridge:
    def __init__(self, sim: Simulator, adapter: Adapter, mode: str = "controller") -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if mode in ("devices", "hybrid") and not adapter.caps.sniff:
            raise ValueError(f"{adapter.name} cannot sniff the bus; mode {mode!r} needs that")
        self.sim = sim
        self.adapter = adapter
        self.mode = mode
        self.source = f"hw:{adapter.name}"
        self._decoder = FrameDecoder()
        self._tasks: set[asyncio.Task] = set()
        self.warnings: list[str] = []
        self.answered = 0
        self.unanswerable = 0
        self.error: str | None = None

    async def start(self) -> None:
        try:
            await self.adapter.open()
        except Exception as exc:  # keep the simulator running without hardware
            self.error = f"could not open {self.adapter.name}: {exc}"
            self.sim.bus.info(self.error, source=self.source)
            return
        self.adapter.listeners.append(self._observed)
        self.sim.bus.info(f"bridge to {self.adapter.name} started in {self.mode} mode", source=self.source)
        if self.mode == "devices" and not self.adapter.caps.send_backward:
            self._warn(f"{self.adapter.name} cannot send backward frames: virtual devices follow the real "
                       "controller's commands but cannot answer its queries")

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.adapter.connected:
            await self.adapter.close()

    def _warn(self, text: str) -> None:
        self.warnings.append(text)
        self.sim.bus.info(text, source=self.source)

    # -- frames seen on the real bus --------------------------------------------
    def _observed(self, obs: Observed) -> None:
        bus = self.sim.bus
        if obs.text == "sent":
            # Our own transmissions: show them, nothing else.
            if self.mode == "controller":
                bus.record(BusRecord(bus.now(), "forward", "[hw] " + self._decoder.decode(obs.frame).text,
                                     obs.frame, self.source))
            return
        if obs.kind == "forward" and obs.frame is not None:
            if self.mode == "controller":
                bus.record(BusRecord(bus.now(), "forward", "[hw] " + self._decoder.decode(obs.frame).text,
                                     obs.frame, "hw:bus"))
                return
            reply = bus.deliver(obs.frame, source="hw:bus", note="from real bus")
            if reply.answered and self.mode == "devices":
                if self.adapter.caps.send_backward:
                    self.answered += 1
                    self._spawn(self.adapter.send_backward(reply.value))
                else:
                    self.unanswerable += 1
        elif obs.kind == "backward" and obs.frame is not None:
            bus.record(BusRecord(bus.now(), "backward", f"[hw] answer 0x{obs.frame.value:02X}", obs.frame, "hw:bus"))
        elif obs.kind == "framing_error":
            bus.record(BusRecord(bus.now(), "framing_error", "[hw] framing error", None, "hw:bus"))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # -- transport for controllers ---------------------------------------------------
    async def send(self, frames: Frame | list[Frame], **kw: Any) -> Reply:
        if self.mode == "controller":
            return await self.adapter.send(frames, expect_answer=kw.get("expect_answer"))
        if self.mode == "hybrid":
            virtual, real = await asyncio.gather(self.sim.bus.send(frames, **kw),
                                                 self.adapter.send(frames, expect_answer=kw.get("expect_answer")))
            return merge_replies(virtual, real)
        return await self.sim.bus.send(frames, **kw)

    def describe(self) -> dict[str, Any]:
        d = self.adapter.describe()
        d.update(mode=self.mode, warnings=self.warnings[-5:], error=self.error, answered=self.answered,
                 unanswerable=self.unanswerable)
        return d


def merge_replies(a: Reply, b: Reply) -> Reply:
    """Combine answers from two halves of one logical bus."""
    if not a.yes:
        return b
    if not b.yes:
        return a
    if a.collision or b.collision:
        return Reply("framing_error", None, a.responders + b.responders)
    res = resolve_collision([Frame.backward(a.value), Frame.backward(b.value)])
    if res is None:
        return Reply("framing_error", None, a.responders + b.responders)
    return Reply("answer", res.value, a.responders + b.responders)
