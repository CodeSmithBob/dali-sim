"""Clocks and a small timer scheduler.

The simulator runs on *simulation time*.  :class:`ManualClock` only moves when
told to (deterministic, used by tests and batch runs).  :class:`RealtimeClock`
follows the wall clock, optionally sped up or slowed down.
"""

from __future__ import annotations

import heapq
import itertools
import time
from collections.abc import Callable


class Clock:
    realtime = False

    def now(self) -> float:  # pragma: no cover - interface
        raise NotImplementedError


class ManualClock(Clock):
    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def set(self, t: float) -> None:
        if t > self._t:
            self._t = t


class RealtimeClock(Clock):
    realtime = True

    def __init__(self, speed: float = 1.0) -> None:
        self._speed = speed
        self._base_real = time.monotonic()
        self._base_sim = 0.0

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        if value <= 0:
            raise ValueError("speed must be > 0")
        now = self.now()
        self._base_real = time.monotonic()
        self._base_sim = now
        self._speed = value

    def now(self) -> float:
        return self._base_sim + (time.monotonic() - self._base_real) * self._speed

    def real_delay(self, sim_seconds: float) -> float:
        return max(0.0, sim_seconds / self._speed)


class Scheduler:
    """Timers in simulation time.  Callbacks receive the time they were due."""

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Callable[[float], None]]] = []
        self._seq = itertools.count()
        self._cancelled: set[int] = set()
        #: Called with the exception when a timer callback fails.
        self.on_error: Callable[[Exception], None] | None = None

    def call_at(self, t: float, fn: Callable[[float], None]) -> int:
        handle = next(self._seq)
        heapq.heappush(self._heap, (t, handle, fn))
        return handle

    def cancel(self, handle: int | None) -> None:
        if handle is not None:
            self._cancelled.add(handle)

    def next_due(self) -> float | None:
        while self._heap and self._heap[0][1] in self._cancelled:
            _, h, _ = heapq.heappop(self._heap)
            self._cancelled.discard(h)
        return self._heap[0][0] if self._heap else None

    def run_due(self, t: float, on_time: Callable[[float], None] | None = None) -> int:
        """Run every timer due at or before ``t`` in order; returns the count.

        ``on_time`` is called with each timer's due time before it runs, so a
        manual clock can step forward through them.
        """
        count = 0
        while True:
            due = self.next_due()
            if due is None or due > t:
                return count
            _, handle, fn = heapq.heappop(self._heap)
            if on_time:
                on_time(due)
            try:
                fn(due)
            except Exception as exc:  # one faulty timer must not stop the others
                if self.on_error:
                    self.on_error(exc)
                else:
                    raise
            count += 1
