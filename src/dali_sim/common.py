"""Behaviour shared by control gear (part 102) and control devices (part 103):
send-twice handling, random addressing, and the bus participant basics."""

from __future__ import annotations

import random
from enum import Enum
from typing import TYPE_CHECKING

from .protocol import timing as T
from .protocol.frames import Frame

if TYPE_CHECKING:  # pragma: no cover
    from .bus import Bus

MASK = 0xFF
YES = 0xFF


class InitState(str, Enum):
    DISABLED = "disabled"
    ENABLED = "enabled"
    WITHDRAWN = "withdrawn"


class TwiceTracker:
    """Implements the "send twice" rule: a configuration command executes
    only if the identical frame arrives again within 100 ms, with no other
    frame in between."""

    def __init__(self) -> None:
        self._pending: tuple[Frame, float] | None = None

    def other_frame(self, frame: Frame) -> None:
        if self._pending and self._pending[0] != frame:
            self._pending = None

    def confirm(self, frame: Frame, t: float) -> bool:
        """Call for a frame that needs sending twice.  True on the repeat."""
        if self._pending and self._pending[0] == frame:
            first_t = self._pending[1]
            self._pending = None
            # Both times are frame end times; allow for the frame duration.
            if t - first_t <= T.SEND_TWICE_TIMEOUT + T.frame_duration(frame.bits):
                return True
        self._pending = (frame, t)
        return False

    def clear(self) -> None:
        self._pending = None


class Unit:
    """Base for a simulated bus unit (control gear or control device)."""

    kind = "unit"

    def __init__(self, name: str, *, short_address: int | None = None, random_address: int | None = None,
                 seed: int | None = None) -> None:
        self.name = name
        self.bus: Bus | None = None
        self.rng = random.Random(seed)
        self.short_address = MASK if short_address is None else short_address
        self.random_address = 0xFFFFFF if random_address is None else random_address & 0xFFFFFF
        self.search_address = 0xFFFFFF
        self.dtr0 = 0
        self.dtr1 = 0
        self.dtr2 = 0
        self.init_state = InitState.DISABLED
        self._init_timer: int | None = None
        self.twice = TwiceTracker()
        self.mains = True  # unit has (mains) power
        self.identify_until = -1.0
        self.power_cycle_seen = True
        self.write_enabled = False
        #: Optional label/position for visualisation.
        self.meta: dict = {}

    # bus wiring ---------------------------------------------------------
    def bind(self, bus: Bus) -> None:
        self.bus = bus

    @property
    def online(self) -> bool:
        return self.mains and (self.bus is None or self.bus.powered)

    # initialisation state -------------------------------------------------
    def _start_initialise(self, t: float) -> None:
        self.init_state = InitState.ENABLED
        if self.bus:
            self.bus.cancel(self._init_timer)
            self._init_timer = self.bus.call_at(t + T.INITIALISE_TIMEOUT, self._init_timeout)

    def _init_timeout(self, t: float) -> None:
        self.init_state = InitState.DISABLED
        self._init_timer = None

    def _terminate(self) -> None:
        self.init_state = InitState.DISABLED
        if self.bus:
            self.bus.cancel(self._init_timer)
        self._init_timer = None

    def _randomise(self) -> None:
        self.random_address = self.rng.randrange(0, 0xFFFFFF)

    def _set_search_byte(self, which: int, value: int) -> None:
        shift = {0: 16, 1: 8, 2: 0}[which]
        self.search_address = (self.search_address & ~(0xFF << shift)) | ((value & 0xFF) << shift)

    @property
    def is_identifying(self) -> bool:
        return self.bus is not None and self.bus.now() < self.identify_until

    def _identify(self, t: float) -> None:
        self.identify_until = t + T.IDENTIFY_DURATION

    def on_bus_power(self, powered: bool, t: float) -> None:  # pragma: no cover - overridden
        pass

    def set_mains(self, on: bool) -> None:
        raise NotImplementedError
