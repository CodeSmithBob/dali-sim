"""Common interface of hardware adapters (USB, serial, GPIO...).

An adapter connects the simulator to a *real* DALI bus.  What it can do
depends on the hardware, described by :class:`Capabilities`:

* ``send_forward``: transmit forward frames and report the answer (every
  adapter can do this; it is what a controller needs).
* ``sniff``: report forward frames that *other* transmitters put on the bus.
  Needed when a real controller should drive virtual devices.
* ``send_backward``: transmit a backward frame on request, within the
  5.5..10.5 ms window after a forward frame.  Needed for virtual devices to
  *answer* a real controller.  Few off-the-shelf adapters can do this.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.decode import FrameDecoder
from ..protocol.frames import Frame


@dataclass
class Capabilities:
    send_forward: bool = True
    forward24: bool = False
    sniff: bool = False
    send_backward: bool = False
    timestamps: bool = False
    bus_power: bool = False
    #: The interface can send a frame twice itself with correct timing.
    native_twice: bool = False
    notes: str = ""


@dataclass
class Observed:
    """A frame seen on the real bus."""

    frame: Frame | None
    kind: str  # forward | backward | framing_error | info
    t: float = field(default_factory=time.monotonic)
    text: str = ""


ObservedFn = Callable[[Observed], None]


class AdapterError(RuntimeError):
    pass


class Adapter:
    """Base class.  Subclasses implement :meth:`_open`, :meth:`_close` and
    :meth:`_transact` (and :meth:`_send_backward` if they can)."""

    name = "adapter"
    caps = Capabilities()

    def __init__(self, **options: Any) -> None:
        self.options = options
        self.listeners: list[ObservedFn] = []
        self._lock = asyncio.Lock()
        self._decoder = FrameDecoder()
        self.connected = False

    # lifecycle ------------------------------------------------------------
    async def open(self) -> None:
        await self._open()
        self.connected = True

    async def close(self) -> None:
        self.connected = False
        await self._close()

    async def __aenter__(self) -> Adapter:
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # controller side --------------------------------------------------------
    async def send(self, frames: Frame | list[Frame], *, expect_answer: bool | None = None, **_: Any) -> Reply:
        """Transport API (same as :meth:`dali_sim.bus.Bus.send`)."""
        if isinstance(frames, Frame):
            frames = [frames]
        reply = NO_ANSWER
        async with self._lock:
            i = 0
            while i < len(frames):
                f = frames[i]
                if f.bits == 24 and not self.caps.forward24:
                    raise AdapterError(f"{self.name} cannot send 24-bit frames")
                # A repeated frame is a send-twice command: use the interface's
                # own send-twice feature when it has one (exact timing).
                twice = self.caps.native_twice and i + 1 < len(frames) and frames[i + 1] == f
                last = i + (2 if twice else 1) >= len(frames)
                decoded = self._decoder.decode(f)
                if twice:
                    self._decoder.decode(f)
                expect = (expect_answer if expect_answer is not None else decoded.expects_answer) if last else False
                reply = await self._transact(f, expect, twice)
                for _ in range(2 if twice else 1):
                    self.emit(Observed(f, "forward", text="sent"))
                if reply.answered:
                    self.emit(Observed(Frame.backward(reply.value), "backward"))
                elif reply.collision:
                    self.emit(Observed(None, "framing_error"))
                i += 2 if twice else 1
        return reply

    async def send_backward(self, value: int) -> None:
        if not self.caps.send_backward:
            raise AdapterError(f"{self.name} cannot transmit backward frames")
        await self._send_backward(value & 0xFF)

    # observation ---------------------------------------------------------------
    def emit(self, obs: Observed) -> None:
        for fn in list(self.listeners):
            try:
                fn(obs)
            except Exception:
                pass

    # to implement ---------------------------------------------------------------
    async def _open(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def _close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        """Send ``frame`` (twice if ``twice``, only when caps.native_twice)
        and return the answer to it (or to the repeat)."""
        raise NotImplementedError  # pragma: no cover

    async def _send_backward(self, value: int) -> None:  # pragma: no cover
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        c = self.caps
        return {"adapter": self.name, "connected": self.connected, "forward24": c.forward24, "sniff": c.sniff,
                "send_backward": c.send_backward, "notes": c.notes}
