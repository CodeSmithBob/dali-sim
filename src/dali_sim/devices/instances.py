"""Input instances of control devices (IEC 62386-301..304).

Each instance has an input value, an event filter, and timers that decide
which events go out on the bus.  The ``press()``, ``set_value()``,
``motion()`` and ``set_lux()`` methods are the "physical world" side: the web
UI and scenario scripts call them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import MASK
from ..protocol.commands import (
    ABSOLUTE_INPUT_COMMANDS,
    LIGHT_SENSOR_COMMANDS,
    OCCUPANCY_COMMANDS,
    PUSH_BUTTON_COMMANDS,
    Cmd,
)
from ..protocol.events import PUSH_BUTTON_FILTER_BITS, PushButtonEvent, occupancy_info

if TYPE_CHECKING:  # pragma: no cover
    from .control_device import ControlDevice


class Instance:
    instance_type = 0
    resolution = 8
    filter_mask = 0xFFFFFF
    default_filter = 0xFFFFFF
    default_priority = 4

    def __init__(self, label: str = "") -> None:
        self.label = label
        self.number = 0
        self.device: ControlDevice | None = None
        self.enabled = True
        self.error = False
        self.error_code = 0
        self.active = True
        self.event_priority = self.default_priority
        self.primary_group = MASK
        self.group1 = MASK
        self.group2 = MASK
        self.event_scheme = 0
        self.event_filter = self.default_filter
        self.input_value = 0
        self._latch: list[int] = []
        self.events_sent = 0
        self.last_event: str | None = None

    # -- wiring ---------------------------------------------------------------
    @property
    def bus(self):
        return self.device.bus if self.device else None

    def now(self) -> float:
        return self.bus.now() if self.bus else 0.0

    def bound(self) -> None:
        """Called when the owning device is attached to a bus."""

    def jitter(self, seconds: float) -> float:
        """Real devices' timers drift apart; without this, sensors started
        together would report at the same moment forever and always collide."""
        rng = self.device.rng if self.device else None
        return seconds * (rng.uniform(0.97, 1.03) if rng else 1.0)

    def type_commands(self) -> dict[int, Cmd]:
        return {}

    def type_command(self, op: int, t: float, confirmed: bool) -> int | None:
        return None

    def reset(self) -> None:
        self.enabled = True
        self.event_priority = self.default_priority
        self.primary_group = self.group1 = self.group2 = MASK
        self.event_scheme = 0
        self.event_filter = self.default_filter

    def reset_state(self) -> bool:
        return (self.enabled and self.event_priority == self.default_priority and self.primary_group == MASK
                and self.group1 == MASK and self.group2 == MASK and self.event_scheme == 0
                and self.event_filter == self.default_filter)

    # -- input value ------------------------------------------------------------
    def value_bytes(self) -> list[int]:
        nbytes = max(1, (self.resolution + 7) // 8)
        # The value is left-aligned: the most significant byte comes first.
        v = self.input_value << (nbytes * 8 - self.resolution)
        return [(v >> (8 * (nbytes - 1 - i))) & 0xFF for i in range(nbytes)]

    def query_input_value(self) -> int:
        b = self.value_bytes()
        self._latch = b[1:]
        return b[0]

    def query_input_latch(self) -> int | None:
        if not self._latch:
            return None
        return self._latch.pop(0)

    # -- events -----------------------------------------------------------------
    def send(self, info: int, name: str) -> None:
        if self.device and self.device.emit(self, info, name.replace("_", " ").lower()):
            self.events_sent += 1
            self.last_event = name

    def snapshot(self, t: float) -> dict[str, Any]:
        return {
            "number": self.number,
            "type": self.instance_type,
            "label": self.label,
            "enabled": self.enabled,
            "value": self.input_value,
            "resolution": self.resolution,
            "events_sent": self.events_sent,
            "last_event": self.last_event,
            "primary_group": None if self.primary_group == MASK else self.primary_group,
            "scheme": self.event_scheme,
            "priority": self.event_priority,
        }


# ---------------------------------------------------------------------------
# 301: push buttons
# ---------------------------------------------------------------------------

class PushButton(Instance):
    instance_type = 1
    resolution = 1
    filter_mask = 0xFF
    #: short press, long press start/repeat/stop, stuck/free
    default_filter = 0b1111_0100
    default_priority = 3

    def __init__(self, label: str = "", *, short_timer: int = 25, double_timer: int = 0, repeat_timer: int = 8,
                 stuck_timer: int = 20) -> None:
        super().__init__(label)
        self.short_timer = short_timer  # x 20 ms
        self.double_timer = double_timer  # x 20 ms, 0 = double press detection off
        self.repeat_timer = repeat_timer  # x 20 ms
        self.stuck_timer = stuck_timer  # seconds
        self.short_timer_min = 10
        self.double_timer_min = 5
        self.pressed = False
        self._state = "idle"  # idle | pressed | long | stuck | wait_double
        self._timer: int | None = None
        self._stuck: int | None = None
        self._defaults = (short_timer, double_timer, repeat_timer, stuck_timer)

    def type_commands(self) -> dict[int, Cmd]:
        return PUSH_BUTTON_COMMANDS

    def reset(self) -> None:
        super().reset()
        self.short_timer, self.double_timer, self.repeat_timer, self.stuck_timer = self._defaults

    def type_command(self, op: int, t: float, confirmed: bool) -> int | None:
        d = self.device
        if op == 0x00 and confirmed:
            self.short_timer = max(d.dtr0, self.short_timer_min)
        elif op == 0x01 and confirmed:
            self.double_timer = 0 if d.dtr0 == 0 else max(d.dtr0, self.double_timer_min)
        elif op == 0x02 and confirmed:
            self.repeat_timer = max(d.dtr0, 1)
        elif op == 0x03 and confirmed:
            self.stuck_timer = max(d.dtr0, 1)
        elif op == 0x0A:
            return self.short_timer
        elif op == 0x0B:
            return self.short_timer_min
        elif op == 0x0C:
            return self.double_timer
        elif op == 0x0D:
            return self.double_timer_min
        elif op == 0x0E:
            return self.repeat_timer
        elif op == 0x0F:
            return self.stuck_timer
        return None

    def _event(self, ev: PushButtonEvent) -> None:
        if self.event_filter & (1 << PUSH_BUTTON_FILTER_BITS[ev]):
            self.send(int(ev), ev.name)

    def _set_timer(self, delay: float, fn) -> None:
        bus = self.bus
        if bus is None:
            return
        bus.cancel(self._timer)
        self._timer = bus.call_later(delay, fn)

    # physical side --------------------------------------------------------------
    def press(self) -> None:
        if self.pressed:
            return
        self.pressed = True
        self.input_value = 1
        self._event(PushButtonEvent.PRESSED)
        if self._state == "wait_double":
            if self.bus:
                self.bus.cancel(self._timer)
            self._event(PushButtonEvent.DOUBLE_PRESS)
            self._state = "double"
        else:
            self._state = "pressed"
            self._set_timer(self.short_timer * 0.02, self._long_start)
        if self.bus:
            self.bus.cancel(self._stuck)
            self._stuck = self.bus.call_later(self.stuck_timer, self._stuck_fire)

    def release(self) -> None:
        if not self.pressed:
            return
        self.pressed = False
        self.input_value = 0
        if self.bus:
            self.bus.cancel(self._stuck)
        self._event(PushButtonEvent.RELEASED)
        if self._state == "pressed":
            if self.bus:
                self.bus.cancel(self._timer)
            if self.double_timer:
                self._state = "wait_double"
                self._set_timer(self.double_timer * 0.02, self._double_timeout)
            else:
                self._event(PushButtonEvent.SHORT_PRESS)
                self._state = "idle"
        elif self._state == "long":
            if self.bus:
                self.bus.cancel(self._timer)
            self._event(PushButtonEvent.LONG_PRESS_STOP)
            self._state = "idle"
        elif self._state == "stuck":
            self._event(PushButtonEvent.BUTTON_FREE)
            self._state = "idle"
        else:
            self._state = "idle"

    def click(self, hold: float = 0.1) -> None:
        """Press now and release after ``hold`` seconds (simulation time)."""
        self.press()
        if self.bus:
            self.bus.call_later(hold, lambda t: self.release())

    def _double_timeout(self, t: float) -> None:
        if self._state == "wait_double":
            self._event(PushButtonEvent.SHORT_PRESS)
            self._state = "idle"

    def _long_start(self, t: float) -> None:
        if self._state != "pressed" or not self.pressed:
            return
        self._state = "long"
        self._event(PushButtonEvent.LONG_PRESS_START)
        self._set_timer(self.repeat_timer * 0.02, self._long_repeat)

    def _long_repeat(self, t: float) -> None:
        if self._state != "long" or not self.pressed:
            return
        self._event(PushButtonEvent.LONG_PRESS_REPEAT)
        self._set_timer(self.repeat_timer * 0.02, self._long_repeat)

    def _stuck_fire(self, t: float) -> None:
        if self.pressed:
            if self.bus:
                self.bus.cancel(self._timer)
            self._state = "stuck"
            self._event(PushButtonEvent.BUTTON_STUCK)

    def snapshot(self, t: float) -> dict[str, Any]:
        d = super().snapshot(t)
        d.update(kind="push-button", pressed=self.pressed, state=self._state)
        return d


# ---------------------------------------------------------------------------
# 302: absolute input devices (sliders, rotary knobs, switches)
# ---------------------------------------------------------------------------

class AbsoluteInput(Instance):
    instance_type = 2
    filter_mask = 0x01
    default_filter = 0x01
    default_priority = 3

    def __init__(self, label: str = "", *, resolution: int = 8, switch: bool = False) -> None:
        super().__init__(label)
        self.resolution = resolution
        self.switch = switch
        self.report_timer = 0  # seconds, 0 = off
        self.deadtime = 2  # x 50 ms
        self._last_sent = -1.0
        self._pending: int | None = None
        self._report: int | None = None

    def type_commands(self) -> dict[int, Cmd]:
        return ABSOLUTE_INPUT_COMMANDS

    def type_command(self, op: int, t: float, confirmed: bool) -> int | None:
        d = self.device
        if op == 0x00 and confirmed:
            self.report_timer = d.dtr0
            self._arm_report()
        elif op == 0x01 and confirmed:
            self.deadtime = d.dtr0
        elif op == 0x0C:
            return self.deadtime
        elif op == 0x0E:
            return self.report_timer
        elif op == 0x0F:
            return 0xFF if self.switch else 0x00
        return None

    def _info(self) -> int:
        """Top 10 bits of the input value."""
        if self.resolution >= 10:
            return self.input_value >> (self.resolution - 10)
        return self.input_value << (10 - self.resolution)

    def set_value(self, value: int) -> None:
        value = max(0, min((1 << self.resolution) - 1, int(value)))
        if value == self.input_value:
            return
        self.input_value = value
        t = self.now()
        wait = self._last_sent + self.deadtime * 0.05 - t
        if wait > 0 and self.bus:
            if self._pending is None:
                self._pending = self.bus.call_later(wait, self._flush)
        else:
            self._flush(t)

    def set_fraction(self, fraction: float) -> None:
        self.set_value(round(fraction * ((1 << self.resolution) - 1)))

    def _flush(self, t: float) -> None:
        self._pending = None
        self._last_sent = t
        if self.event_filter & 1:
            self.send(self._info(), f"position {self.input_value}")

    def _arm_report(self) -> None:
        if self.bus is None:
            return
        self.bus.cancel(self._report)
        self._report = None
        if self.report_timer:
            self._report = self.bus.call_later(self.jitter(self.report_timer), self._report_fire)

    def _report_fire(self, t: float) -> None:
        self._report = None
        self._flush(t)
        self._arm_report()

    def bound(self) -> None:
        self._arm_report()

    def snapshot(self, t: float) -> dict[str, Any]:
        d = super().snapshot(t)
        d.update(kind="absolute-input", max=(1 << self.resolution) - 1)
        return d


# ---------------------------------------------------------------------------
# 303: occupancy sensors
# ---------------------------------------------------------------------------

class OccupancySensor(Instance):
    instance_type = 3
    resolution = 2
    filter_mask = 0b11111
    #: bit 0 occupied, 1 vacant, 2 repeat, 3 movement, 4 no movement
    default_filter = 0b00111
    default_priority = 4

    def __init__(self, label: str = "", *, hold_timer: int = 90, report_timer: int = 20, deadtime: int = 2,
                 presence: bool = False) -> None:
        super().__init__(label)
        self.hold_timer = hold_timer  # x 10 s
        self.report_timer = report_timer  # seconds
        self.deadtime = deadtime  # x 50 ms
        self.presence = presence
        self.occupied = False
        self.movement = False
        self.catching = False
        self._hold: int | None = None
        self._report: int | None = None
        self._defaults = (hold_timer, report_timer, deadtime)

    def type_commands(self) -> dict[int, Cmd]:
        return OCCUPANCY_COMMANDS

    def reset(self) -> None:
        super().reset()
        self.hold_timer, self.report_timer, self.deadtime = self._defaults

    def type_command(self, op: int, t: float, confirmed: bool) -> int | None:
        d = self.device
        if op == 0x00:
            self.catching = True
        elif op == 0x01 and confirmed:
            self.hold_timer = max(1, d.dtr0)
        elif op == 0x02 and confirmed:
            self.report_timer = d.dtr0
        elif op == 0x03 and confirmed:
            self.deadtime = d.dtr0
        elif op == 0x04:
            if self.bus:
                self.bus.cancel(self._hold)
            self._vacant(t)
        elif op == 0x0C:
            return self.deadtime
        elif op == 0x0D:
            return self.hold_timer
        elif op == 0x0E:
            return self.report_timer
        elif op == 0x0F:
            return 0xFF if self.catching else None
        return None

    def _update_value(self) -> None:
        self.input_value = (int(self.occupied) << 1) | int(self.movement)

    def _info(self, repeat: bool = False) -> int:
        return occupancy_info(self.occupied, self.movement, repeat, self.presence)

    # physical side --------------------------------------------------------
    def motion(self, moving: bool = True) -> None:
        """Movement starts (True) or stops (False) in front of the sensor."""
        bus = self.bus
        was_occupied = self.occupied
        self.movement = moving
        if moving:
            self.occupied = True
            self.catching = False
            if bus:
                bus.cancel(self._hold)
                self._hold = None
        elif bus and self.occupied:
            bus.cancel(self._hold)
            self._hold = bus.call_later(self.hold_timer * 10, self._vacant)
        self._update_value()
        if moving and not was_occupied:
            if self.event_filter & 1:
                self.send(self._info(), "occupied")
        elif self.event_filter & (1 << (3 if moving else 4)):
            self.send(self._info(), "movement" if moving else "no movement")
        self._arm_report()

    def _vacant(self, t: float) -> None:
        self._hold = None
        if not self.occupied:
            return
        self.occupied = False
        self.movement = False
        self._update_value()
        if self.event_filter & 2:
            self.send(self._info(), "vacant")
        self._arm_report()

    def _arm_report(self) -> None:
        bus = self.bus
        if bus is None:
            return
        bus.cancel(self._report)
        self._report = None
        if self.report_timer:
            self._report = bus.call_later(self.jitter(self.report_timer), self._report_fire)

    def _report_fire(self, t: float) -> None:
        self._report = None
        if self.event_filter & 4:
            self.send(self._info(repeat=True), "still occupied" if self.occupied else "still vacant")
        self._arm_report()

    def bound(self) -> None:
        self._arm_report()

    def snapshot(self, t: float) -> dict[str, Any]:
        d = super().snapshot(t)
        d.update(kind="occupancy", occupied=self.occupied, movement=self.movement, hold_s=self.hold_timer * 10)
        return d


# ---------------------------------------------------------------------------
# 304: light sensors
# ---------------------------------------------------------------------------

class LightSensor(Instance):
    instance_type = 4
    filter_mask = 0x01
    default_filter = 0x01
    default_priority = 4

    def __init__(self, label: str = "", *, resolution: int = 10, max_lux: float = 2000.0, hysteresis: int = 5,
                 deadtime: int = 30, report_timer: int = 30) -> None:
        super().__init__(label)
        self.resolution = resolution
        self.max_lux = max_lux
        self.hysteresis = hysteresis  # percent
        self.hysteresis_min = 10  # in input-value units
        self.deadtime = deadtime  # x 50 ms
        self.report_timer = report_timer  # seconds
        self.lux = 0.0
        self._last_reported: int | None = None
        self._last_sent = -1e9
        self._pending: int | None = None
        self._report: int | None = None
        self._defaults = (hysteresis, deadtime, report_timer)

    def type_commands(self) -> dict[int, Cmd]:
        return LIGHT_SENSOR_COMMANDS

    def reset(self) -> None:
        super().reset()
        self.hysteresis, self.deadtime, self.report_timer = self._defaults

    def type_command(self, op: int, t: float, confirmed: bool) -> int | None:
        d = self.device
        if op == 0x00 and confirmed:
            self.report_timer = d.dtr0
            self._arm_report()
        elif op == 0x01 and confirmed:
            self.hysteresis = min(d.dtr0, 25)
        elif op == 0x02 and confirmed:
            self.deadtime = d.dtr0
        elif op == 0x03 and confirmed:
            self.hysteresis_min = d.dtr0
        elif op == 0x0C:
            return self.hysteresis_min
        elif op == 0x0D:
            return self.deadtime
        elif op == 0x0E:
            return self.report_timer
        elif op == 0x0F:
            return self.hysteresis
        return None

    def _info(self) -> int:
        if self.resolution >= 10:
            return self.input_value >> (self.resolution - 10)
        return self.input_value << (10 - self.resolution)

    def set_lux(self, lux: float) -> None:
        self.lux = max(0.0, float(lux))
        full = (1 << self.resolution) - 1
        self.input_value = min(full, int(round(self.lux / self.max_lux * full)))
        last = self._last_reported
        band = max(self.hysteresis_min, int(abs(last or 0) * self.hysteresis / 100)) if last is not None else 0
        if last is not None and abs(self.input_value - last) < band:
            return
        t = self.now()
        wait = self._last_sent + self.deadtime * 0.05 - t
        if wait > 0 and self.bus:
            if self._pending is None:
                self._pending = self.bus.call_later(wait, self._flush)
        else:
            self._flush(t)

    def _flush(self, t: float) -> None:
        self._pending = None
        self._last_sent = t
        self._last_reported = self.input_value
        if self.event_filter & 1:
            self.send(self._info(), f"{self.lux:.0f} lx")
        self._arm_report()

    def _arm_report(self) -> None:
        bus = self.bus
        if bus is None:
            return
        bus.cancel(self._report)
        self._report = None
        if self.report_timer:
            self._report = bus.call_later(self.jitter(self.report_timer), lambda t: self._flush(t))

    def bound(self) -> None:
        self._arm_report()

    def snapshot(self, t: float) -> dict[str, Any]:
        d = super().snapshot(t)
        d.update(kind="light-sensor", lux=round(self.lux, 1), max_lux=self.max_lux)
        return d
