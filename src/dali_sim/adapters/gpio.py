"""Bit-banged DALI on Raspberry Pi GPIO, using the pigpio daemon.

EXPERIMENTAL.  Needs a DALI physical interface between the Pi and the bus:
an opto-isolated receiver on ``rx`` and a transistor stage on ``tx`` that pulls
the bus low (for example a "DALI click" style board, or the open designs
listed in docs/hardware.md).  Most such boards invert one or both signals;
set ``rx_invert`` / ``tx_invert`` to match.

pigpio times waveforms with DMA and timestamps edges in microseconds, which
is precise enough for DALI's 416 us half bits.  Answering as virtual control
gear (``send_backward``) depends on Python reacting within the 5.5..10.5 ms
window, which a lightly loaded Pi usually manages.

Start the daemon first: ``sudo pigpiod``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol import timing as T
from ..protocol.frames import Frame, ManchesterError
from ..protocol.manchester import decode_pulses, encode_pulses
from .base import Adapter, AdapterError, Capabilities, Observed

IDLE_GAP_US = 2000  # a high period this long ends a frame


class GpioAdapter(Adapter):
    name = "gpio"
    caps = Capabilities(forward24=True, sniff=True, send_backward=True, timestamps=True,
                        notes="experimental: pigpio bit-banging on Raspberry Pi GPIO")

    def __init__(self, tx: int = 17, rx: int = 27, tx_invert: int = 0, rx_invert: int = 1,
                 pi: Any = None, host: str = "localhost", port: int = 8888, **options: Any) -> None:
        super().__init__(**options)
        self.tx, self.rx = int(tx), int(rx)
        self.tx_invert, self.rx_invert = bool(tx_invert), bool(rx_invert)
        self._pi = pi
        self._host, self._port = host, port
        self._pulses: list[tuple[int, float]] = []
        self._last_tick: int | None = None
        self._last_level = 1
        self._frames: asyncio.Queue[tuple[Frame | None, float]] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cb = None
        self._sending: Frame | None = None
        self._last_forward_end = 0.0
        self.idle_since = time.monotonic()

    # -- lifecycle ------------------------------------------------------------
    async def _open(self) -> None:
        if self._pi is None:
            try:
                import pigpio  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise AdapterError("pigpio is needed: pip install 'dali-sim[gpio]' and run pigpiod") from exc
            self._pi = pigpio.pi(self._host, self._port)
            if not self._pi.connected:
                raise AdapterError("cannot connect to pigpiod")
            self._pulse = pigpio.pulse
        else:  # an injected pigpio-compatible object (tests, other GPIO libraries)
            self._pulse = self._pi.pulse_class
        pi = self._pi
        self._loop = asyncio.get_running_loop()
        pi.set_mode(self.tx, 1)  # output
        pi.set_mode(self.rx, 0)  # input
        pi.write(self.tx, self._tx_level(1))  # release the bus
        pi.set_watchdog(self.rx, IDLE_GAP_US // 1000 + 1)
        self._cb = pi.callback(self.rx, 2, self._edge)  # either edge

    async def _close(self) -> None:
        if self._cb is not None:
            self._cb.cancel()
        if self._pi is not None:
            self._pi.set_watchdog(self.rx, 0)

    def _tx_level(self, bus_level: int) -> int:
        return (1 - bus_level) if self.tx_invert else bus_level

    def _bus_level(self, pin_level: int) -> int:
        return (1 - pin_level) if self.rx_invert else pin_level

    # -- receiving ----------------------------------------------------------------
    def _edge(self, gpio: int, level: int, tick: int) -> None:
        """pigpio callback (runs in pigpio's thread)."""
        if level == 2:  # watchdog: no edge for a while -> frame complete
            self._finish_frame()
            return
        bus_level = self._bus_level(level)
        if bus_level == self._last_level and self._last_tick is not None:
            return  # not a change (glitch or duplicate report)
        self._last_level = bus_level
        if self._last_tick is not None:
            dur = ((tick - self._last_tick) & 0xFFFFFFFF)
            prev = 1 - bus_level
            if prev == 1 and dur > IDLE_GAP_US:
                self._finish_frame()
            else:
                self._pulses.append((prev, float(dur)))
        self._last_tick = tick

    def _finish_frame(self) -> None:
        pulses, self._pulses = self._pulses, []
        self._last_tick = None
        self._last_level = 1
        if not pulses:
            return
        try:
            frame: Frame | None = decode_pulses(pulses + [(1, 4 * T.TE * 1e6)])
        except ManchesterError:
            frame = None
        now = time.monotonic()
        self.idle_since = now
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._deliver, frame, now)

    def _deliver(self, frame: Frame | None, t: float) -> None:
        self._frames.put_nowait((frame, t))
        if self._sending is not None:
            return  # a transaction of ours is waiting for these frames
        if frame is None:
            self.emit(Observed(None, "framing_error"))
        elif frame.bits == 8:
            self.emit(Observed(frame, "backward"))
        else:
            self._last_forward_end = t
            self.emit(Observed(frame, "forward"))

    # -- transmitting --------------------------------------------------------------
    def _wave(self, frame: Frame) -> int:
        import_pulse = self._pulse
        pulses = []
        on, off = 1 << self.tx, 0
        for level, dur in encode_pulses(frame):
            pin = self._tx_level(level)
            pulses.append(import_pulse(on if pin else off, off if pin else on, int(round(dur))))
        pulses.append(import_pulse(on if self._tx_level(1) else off, off if self._tx_level(1) else on, 100))
        self._pi.wave_clear()
        self._pi.wave_add_generic(pulses)
        return self._pi.wave_create()

    async def _transmit(self, frame: Frame) -> None:
        wid = self._wave(frame)
        self._pi.wave_send_once(wid)
        while self._pi.wave_tx_busy():
            await asyncio.sleep(0.002)
        self._pi.wave_delete(wid)

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        settle = T.FORWARD_SETTLING[3].min
        wait = self.idle_since + settle - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        while not self._frames.empty():
            self._frames.get_nowait()
        self._sending = frame
        try:
            await self._transmit(frame)
            # our own frame comes back through the receiver
            try:
                echo, _ = await asyncio.wait_for(self._frames.get(), 0.05)
            except asyncio.TimeoutError:
                raise AdapterError("transmitted frame not seen on rx: check wiring and bus power") from None
            if echo != frame:
                raise AdapterError("collision: frame on the bus differs from the frame sent")
            try:
                answer, _ = await asyncio.wait_for(self._frames.get(), T.BACKWARD_FRAME_TIMEOUT + IDLE_GAP_US / 1e6)
            except asyncio.TimeoutError:
                return NO_ANSWER
            if answer is None:
                return Reply("framing_error")
            if answer.bits == 8:
                return Reply("answer", answer.value)
            return NO_ANSWER
        finally:
            self._sending = None

    async def _send_backward(self, value: int) -> None:
        late = time.monotonic() - self._last_forward_end
        delay = T.FORWARD_TO_BACKWARD.min - late
        if delay > 0:
            await asyncio.sleep(delay)
        elif late > T.FORWARD_TO_BACKWARD.max:
            raise AdapterError(f"backward frame too late ({late * 1000:.1f} ms)")
        await self._transmit(Frame.backward(value))
