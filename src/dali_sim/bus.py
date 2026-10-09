"""The simulated DALI bus.

Everything that wants to put frames on the bus submits a :class:`Transaction`
(one or more forward frames sent back to back, e.g. a send-twice command or
DTR0 + command).  The bus picks the next transaction the way DALI-2
multi-master arbitration would: the lowest priority number wins because it
waits the shortest settling time; transactions of equal priority from
different transmitters that are ready at the same moment collide and back
off.

Every participant (control gear, control device, monitor, bridge) sees every
frame.  Answers of several participants are superimposed as on a real
wired-AND bus, so different answers turn into a framing error.

The core is synchronous and deterministic.  With a :class:`ManualClock`,
:meth:`Bus.run_until_idle` and :meth:`Bus.advance` drive it; with a
:class:`RealtimeClock`, :meth:`Bus.run` paces it against the wall clock.
"""

from __future__ import annotations

import asyncio
import itertools
import random
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from .clock import Clock, ManualClock, RealtimeClock, Scheduler
from .protocol import timing as T
from .protocol.decode import FrameDecoder
from .protocol.frames import Frame, resolve_collision

# ---------------------------------------------------------------------------
# Results and records
# ---------------------------------------------------------------------------

@dataclass
class Reply:
    """Outcome of one forward frame."""

    status: str  # "none" | "answer" | "framing_error"
    value: int | None = None
    responders: list[str] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.status == "answer"

    @property
    def collision(self) -> bool:
        return self.status == "framing_error"

    @property
    def yes(self) -> bool:
        """For YES/NO queries: any backward frame (even garbled) means YES."""
        return self.status in ("answer", "framing_error")

    def __repr__(self) -> str:
        if self.status == "answer":
            return f"Reply(answer={self.value:#04x}, by={self.responders})"
        if self.status == "framing_error":
            return f"Reply(framing_error, by={self.responders})"
        return "Reply(none)"


NO_ANSWER = Reply("none")


@dataclass
class BusRecord:
    """One line of the bus monitor."""

    t: float
    kind: str  # forward | backward | event | collision | framing_error | power | info
    text: str
    frame: Frame | None = None
    source: str = ""
    duration: float = 0.0
    seq: int = 0

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "t": round(self.t, 6),
            "kind": self.kind,
            "text": self.text,
            "bits": self.frame.bits if self.frame else None,
            "hex": self.frame.hex() if self.frame else None,
            "source": self.source,
            "duration_ms": round(self.duration * 1000, 2),
        }


@dataclass
class Transaction:
    frames: list[Frame]
    source: str = "controller"
    priority: int = 3
    #: Index of the frame whose answer is returned (default: last).
    expect_answer: bool | None = None
    not_before: float = 0.0
    multi_master: bool = False  # True for transmitters that arbitrate (control devices)
    replies: list[Reply] = field(default_factory=list)
    done: bool = False
    future: asyncio.Future | None = None
    seq: int = 0
    attempts: int = 0
    callback: Callable[[Transaction], None] | None = None
    #: Extra text for the bus monitor (e.g. what a simulated event means).
    note: str = ""

    @property
    def reply(self) -> Reply:
        return self.replies[-1] if self.replies else NO_ANSWER


class Participant(Protocol):
    name: str

    def on_frame(self, frame: Frame, t: float, source: str) -> int | None:
        """Called at the end of every forward frame.  Return a backward-frame
        value to answer, or None."""

    def on_bus_power(self, powered: bool, t: float) -> None: ...


@dataclass
class Faults:
    """Fault injection for robustness testing."""

    drop_forward: float = 0.0  # probability a forward frame is not received
    corrupt_backward: float = 0.0  # probability an answer arrives garbled
    drop_backward: float = 0.0  # probability an answer is lost
    seed: int | None = None


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------

class Bus:
    def __init__(self, clock: Clock | None = None, name: str = "bus0", faults: Faults | None = None,
                 history: int = 5000) -> None:
        self.name = name
        self.clock = clock or ManualClock()
        self.scheduler = Scheduler()
        self.scheduler.on_error = self._internal_error
        self.participants: list[Participant] = []
        self.listeners: list[Callable[[BusRecord], None]] = []
        self.frame_listeners: list[Callable[[Frame, float, str, Reply | None], None]] = []
        self.powered = True
        self.faults = faults or Faults()
        self._rng = random.Random(self.faults.seed)
        self._queue: list[Transaction] = []
        self._seq = itertools.count(1)
        self._rec_seq = itertools.count(1)
        self._idle_since = -1.0
        self._busy_until = 0.0
        self._decoder = FrameDecoder()
        self.history: list[BusRecord] = []
        self.errors: list[str] = []
        self._history_max = history
        self._wakeup: asyncio.Event | None = None
        self.stats = {"forward": 0, "backward": 0, "collisions": 0, "framing_errors": 0, "events": 0}

    # -- participants -----------------------------------------------------
    def attach(self, p: Participant) -> Participant:
        self.participants.append(p)
        if hasattr(p, "bind"):
            p.bind(self)
        return p

    def detach(self, p: Participant) -> None:
        self.participants.remove(p)

    def now(self) -> float:
        return self.clock.now()

    # -- timers -----------------------------------------------------------
    def call_at(self, t: float, fn: Callable[[float], None]) -> int:
        handle = self.scheduler.call_at(t, fn)
        self._kick()
        return handle

    def call_later(self, delay: float, fn: Callable[[float], None], base: float | None = None) -> int:
        return self.call_at((self.now() if base is None else base) + delay, fn)

    def cancel(self, handle: int | None) -> None:
        self.scheduler.cancel(handle)

    # -- records ----------------------------------------------------------
    def record(self, rec: BusRecord) -> None:
        rec.seq = next(self._rec_seq)
        self.history.append(rec)
        if len(self.history) > self._history_max:
            del self.history[: len(self.history) - self._history_max]
        for fn in list(self.listeners):
            try:
                fn(rec)
            except Exception:  # listeners must not break the bus
                pass

    def _internal_error(self, exc: Exception) -> None:
        import traceback

        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        self.errors.append("".join(tb))
        self.record(BusRecord(self.now(), "info", f"internal error: {exc!r} (see bus.errors)", source="sim"))

    def info(self, text: str, source: str = "sim") -> None:
        self.record(BusRecord(self.now(), "info", text, source=source))

    # -- power ------------------------------------------------------------
    def set_power(self, powered: bool) -> None:
        if powered == self.powered:
            return
        self.powered = powered
        t = self.now()
        self.record(BusRecord(t, "power", "bus power restored" if powered else "bus power failure"))
        for p in list(self.participants):
            p.on_bus_power(powered, t)

    # -- submitting -------------------------------------------------------
    def submit(self, frames: Frame | Iterable[Frame], source: str = "controller", priority: int = 3,
               expect_answer: bool | None = None, multi_master: bool = False,
               callback: Callable[[Transaction], None] | None = None, note: str = "") -> Transaction:
        if isinstance(frames, Frame):
            frames = [frames]
        if not 1 <= priority <= 5:
            raise ValueError("priority must be 1..5")
        tr = Transaction(list(frames), source=source, priority=priority, expect_answer=expect_answer,
                         multi_master=multi_master, seq=next(self._seq), callback=callback, note=note)
        tr.not_before = self.now()
        self._queue.append(tr)
        self._kick()
        return tr

    def _kick(self) -> None:
        if self._wakeup is not None:
            self._wakeup.set()

    # -- the engine -------------------------------------------------------
    def _settling(self, priority: int) -> float:
        return T.FORWARD_SETTLING[priority].min

    def _pick(self) -> tuple[Transaction | None, list[Transaction], float]:
        """Choose the next transaction.  Returns (winner, colliders, start)."""
        if not self._queue:
            return None, [], 0.0
        candidates = []
        for tr in self._queue:
            start = max(tr.not_before, self._busy_until + self._settling(tr.priority))
            candidates.append((start, tr.priority, tr.seq, tr))
        candidates.sort(key=lambda c: (c[0], c[1], c[2]))
        start, prio, _, winner = candidates[0]
        # Equal-priority multi-master transmitters that become ready within
        # the same settling window start together and collide.
        colliders = [
            c[3] for c in candidates[1:]
            if c[3].multi_master and winner.multi_master and c[1] == prio
            and c[3].source != winner.source and abs(c[0] - start) < 2 * T.TE
        ]
        return winner, colliders, start

    def step(self) -> Transaction | None:
        """Transmit the next transaction (synchronously).  Returns it, or None
        if the queue is empty."""
        winner, colliders, start = self._pick()
        if winner is None:
            return None
        if not self.powered:
            # Nothing can be transmitted without bus power.
            for tr in [winner, *colliders]:
                self._queue.remove(tr)
                tr.replies = [NO_ANSWER for _ in tr.frames]
                self._finish(tr)
            return winner
        self._advance_to(start)
        if colliders:
            self._collide([winner, *colliders], start)
            return winner
        self._queue.remove(winner)
        t = start
        for i, frame in enumerate(winner.frames):
            if i > 0:
                t = max(t, self._busy_until + self._settling(1))
                self._advance_to(t)
            reply, t = self._transmit(frame, t, winner)
            winner.replies.append(reply)
        self._finish(winner)
        return winner

    def _collide(self, trs: list[Transaction], start: float) -> None:
        frames = [tr.frames[0] for tr in trs]
        dur = T.frame_duration(max(f.bits for f in frames))
        self.stats["collisions"] += 1
        self.record(BusRecord(start, "collision",
                              "collision between " + ", ".join(tr.source for tr in trs),
                              source=",".join(tr.source for tr in trs), duration=dur))
        end = start + dur
        self._busy_until = end
        self._advance_to(end)
        for tr in trs:
            tr.attempts += 1
            # Random back-off of a few settling slots, as transmitters do
            # after detecting a collision.
            tr.not_before = end + self._rng.uniform(0, 4) * T.FORWARD_SETTLING[tr.priority].max
            if tr.attempts > 10:
                self._queue.remove(tr)
                tr.replies = [NO_ANSWER]
                self.record(BusRecord(end, "info", f"{tr.source}: gave up after 10 collisions"))
                self._finish(tr)

    def _transmit(self, frame: Frame, t: float, tr: Transaction) -> tuple[Reply, float]:
        dur = T.frame_duration(frame.bits)
        end = t + dur
        decoded = self._decoder.decode(frame)
        kind = "event" if decoded.is_event else "forward"
        self.stats["events" if decoded.is_event else "forward"] += 1
        text = f"{decoded.text} [{tr.note}]" if tr.note else decoded.text
        self.record(BusRecord(t, kind, text, frame, tr.source, dur))
        self._busy_until = end
        self._advance_to(end)
        if self.faults.drop_forward and self._rng.random() < self.faults.drop_forward:
            self.record(BusRecord(end, "info", "fault injection: forward frame lost"))
            for fn in list(self.frame_listeners):
                fn(frame, end, tr.source, None)
            return NO_ANSWER, end
        answers: list[tuple[str, int]] = []
        for p in list(self.participants):
            if getattr(p, "name", None) == tr.source:
                continue  # a transmitter does not answer itself
            try:
                v = p.on_frame(frame, end, tr.source)
            except Exception as exc:  # a faulty model must not kill the bus
                self.record(BusRecord(end, "info", f"{p.name}: error {exc!r}"))
                v = None
            if isinstance(v, list):  # several instances of one unit answered
                answers.extend((p.name, x & 0xFF) for x in v)
            elif v is not None:
                answers.append((p.name, v & 0xFF))
        expect = tr.expect_answer if tr.expect_answer is not None else decoded.expects_answer
        reply = NO_ANSWER
        if answers:
            if self.faults.drop_backward and self._rng.random() < self.faults.drop_backward:
                answers = []
        if answers:
            bstart = end + T.FORWARD_TO_BACKWARD.mid
            bdur = T.BACKWARD_DURATION
            names = [n for n, _ in answers]
            result = resolve_collision([Frame.backward(v) for _, v in answers])
            if result is not None and self.faults.corrupt_backward and self._rng.random() < self.faults.corrupt_backward:
                result = None
            if result is None:
                reply = Reply("framing_error", None, names)
                self.stats["framing_errors"] += 1
                self.record(BusRecord(bstart, "framing_error",
                                      f"framing error ({len(answers)} answers: "
                                      + ", ".join(f"{n}=0x{v:02X}" for n, v in answers) + ")",
                                      None, ",".join(names), bdur))
            else:
                reply = Reply("answer", result.value, names)
                self.stats["backward"] += 1
                who = names[0] if len(names) == 1 else f"{len(names)} devices"
                self.record(BusRecord(bstart, "backward", f"answer 0x{result.value:02X} ({result.value}) from {who}",
                                      result, ",".join(names), bdur))
            end = bstart + bdur
            self._busy_until = end
            self._advance_to(end)
        elif expect:
            end = end + T.BACKWARD_FRAME_TIMEOUT
            self._busy_until = end
            self._advance_to(end)
        for fn in list(self.frame_listeners):
            fn(frame, end, tr.source, reply)
        return reply, end

    def deliver(self, frame: Frame, source: str, expect_answer: bool | None = None, note: str = "") -> Reply:
        """Put a frame on the bus *now*, bypassing the queue and arbitration.

        Used by bridges: a frame seen on a real bus has already won
        arbitration there, and the answer is needed at once.
        """
        tr = Transaction([frame], source=source, expect_answer=expect_answer, note=note)
        t = max(self.now(), self._busy_until)
        reply, _ = self._transmit(frame, t, tr)
        return reply

    def _finish(self, tr: Transaction) -> None:
        tr.done = True
        if tr.callback:
            try:
                tr.callback(tr)
            except Exception:
                pass
        if tr.future is not None and not tr.future.done():
            tr.future.set_result(tr.replies)

    def _advance_to(self, t: float) -> None:
        if isinstance(self.clock, ManualClock):
            self.scheduler.run_due(t, on_time=self.clock.set)
            self.clock.set(t)

    # -- synchronous driving (ManualClock) ---------------------------------
    def run_until_idle(self, max_steps: int = 100000) -> None:
        for _ in range(max_steps):
            if self.step() is None:
                return
        raise RuntimeError("bus did not become idle")

    def advance(self, seconds: float) -> None:
        """Advance a manual clock by ``seconds``, transmitting queued frames
        and firing timers on the way."""
        if not isinstance(self.clock, ManualClock):
            raise TypeError("advance() needs a ManualClock")
        end = self.clock.now() + seconds
        while True:
            self.run_until_idle()
            due = self.scheduler.next_due()
            if due is None or due > end:
                break
            self._advance_to(due)
        self.run_until_idle()
        self._advance_to(end)

    def request(self, frames: Frame | Iterable[Frame], **kw) -> Reply:
        """Submit and run to completion (manual clock).  Returns the reply to
        the last frame."""
        tr = self.submit(frames, **kw)
        while not tr.done:
            if self.step() is None:  # pragma: no cover
                break
        return tr.reply

    # -- asynchronous driving (any clock) ------------------------------------
    async def send(self, frames: Frame | Iterable[Frame], **kw) -> Reply:
        tr = self.submit(frames, **kw)
        loop = asyncio.get_running_loop()
        tr.future = loop.create_future()
        if tr.done:
            return tr.reply
        if self._wakeup is None:
            # No pump running: drive it inline.
            while not tr.done:
                if self.step() is None:  # pragma: no cover
                    break
            return tr.reply
        await tr.future
        return tr.reply

    async def run(self) -> None:
        """Pump the bus in real time.  Run as a background task."""
        self._wakeup = asyncio.Event()
        clock = self.clock
        try:
            while True:
                now = clock.now()
                self.scheduler.run_due(now)
                if self._queue:
                    try:
                        tr = self.step()
                    except Exception as exc:  # keep pumping; report the bug
                        self._internal_error(exc)
                        tr = None
                        await asyncio.sleep(0.01)
                    if tr is not None and isinstance(clock, RealtimeClock):
                        wait = self._busy_until - clock.now()
                        if wait > 0:
                            await asyncio.sleep(clock.real_delay(wait))
                    else:
                        await asyncio.sleep(0)
                    continue
                due = self.scheduler.next_due()
                timeout = 0.25
                if due is not None:
                    delta = due - clock.now()
                    timeout = min(timeout, clock.real_delay(delta) if isinstance(clock, RealtimeClock) else 0)
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=max(timeout, 0.001))
                except asyncio.TimeoutError:
                    pass
        finally:
            self._wakeup = None
