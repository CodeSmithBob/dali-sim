"""A rule-based application controller living on the simulated bus.

It listens to event messages from control devices and sends commands to
control gear, like a real DALI-2 application controller would:

.. code-block:: yaml

    rules:
      - when: {short_address: 0, instance: 0, event: short_press}
        do: {toggle: G0}
      - when: {short_address: 0, instance: 0, event: long_press_repeat}
        do: {up: G0}
      - when: {short_address: 1, instance: 0, event: occupied}
        do: {recall_max: G1}
      - when: {short_address: 1, instance: 0, event: vacant}
        do: {off: G1}
    daylight:
      - {sensor: {short_address: 2, instance: 0}, target: G2, setpoint_lux: 500}

Matching uses only what is inside the event frame (short address, instance
number/type, device/instance group), so it depends on the event scheme the
devices are configured with, just like on a real bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bus import Bus
from .devices.control_device import ControlDevice
from .protocol.address import parse_gear_target
from .protocol.events import Event, PushButtonEvent
from .protocol.frames import Frame

EVENT_NAMES: dict[str, tuple[int | None, int | None]] = {
    # name: (instance type, info) ; info None = any
    "pressed": (1, PushButtonEvent.PRESSED),
    "released": (1, PushButtonEvent.RELEASED),
    "short_press": (1, PushButtonEvent.SHORT_PRESS),
    "double_press": (1, PushButtonEvent.DOUBLE_PRESS),
    "long_press_start": (1, PushButtonEvent.LONG_PRESS_START),
    "long_press_repeat": (1, PushButtonEvent.LONG_PRESS_REPEAT),
    "long_press_stop": (1, PushButtonEvent.LONG_PRESS_STOP),
    "stuck": (1, PushButtonEvent.BUTTON_STUCK),
    "free": (1, PushButtonEvent.BUTTON_FREE),
    "occupied": (3, None),
    "vacant": (3, None),
    "position": (2, None),
    "illuminance": (4, None),
    "any": (None, None),
}


@dataclass
class Rule:
    when: dict[str, Any]
    do: dict[str, Any]
    hits: int = 0

    def matches(self, ev: Event) -> bool:
        w = self.when
        for key, attr in (("short_address", "short_address"), ("instance", "instance_number"),
                          ("instance_type", "instance_type"), ("device_group", "device_group"),
                          ("instance_group", "instance_group")):
            if key in w and getattr(ev, attr) != w[key]:
                return False
        name = w.get("event", "any")
        itype, info = EVENT_NAMES.get(name, (None, None))
        if itype is not None and ev.instance_type is not None and ev.instance_type != itype:
            return False
        if info is not None and ev.info != info:
            return False
        if name == "occupied" and not ev.info & 0b10:
            return False
        if name == "vacant" and ev.info & 0b10:
            return False
        if name in ("occupied", "vacant") and ev.info & 0b100 and not w.get("repeat", False):
            return False
        return True


@dataclass
class DaylightLoop:
    sensor: dict[str, Any]
    target: str
    setpoint_lux: float = 500.0
    max_lux: float = 2000.0
    resolution: int = 10
    gain: float = 0.6
    level: int = 200
    enabled: bool = True
    measured: float = field(default=0.0)


class AutomationController(ControlDevice):
    """An application controller that is also a 103 control device on the
    bus (it can be found by scanning and addressed like any device)."""

    def __init__(self, name: str = "app-controller", *, rules: list[dict] | None = None,
                 daylight: list[dict] | None = None, priority: int = 3, **kw: Any) -> None:
        super().__init__(name, application_controller=True, **kw)
        self.rules = [Rule(r["when"], r["do"]) for r in rules or []]
        self.daylight = [DaylightLoop(**d) for d in daylight or []]
        self.priority = priority
        self._on: dict[str, bool] = {}
        self.log: list[str] = []

    def bind(self, bus: Bus) -> None:  # type: ignore[override]
        super().bind(bus)
        bus.frame_listeners.append(self._observe)

    # -- observing the bus ---------------------------------------------------
    def _observe(self, frame: Frame, t: float, source: str, reply) -> None:
        if source == self.name or not self.app_controller_enabled or frame.bits != 24 or frame.bit(16):
            if frame.bits == 16:
                self._track(frame)
            return
        ev = Event.decode(frame)
        if ev is None:
            return
        for rule in self.rules:
            if rule.matches(ev):
                rule.hits += 1
                self._run(rule.do, ev)
        for loop in self.daylight:
            if self._sensor_match(loop.sensor, ev) and ev.instance_type in (4, None):
                self._daylight(loop, ev)

    def _track(self, frame: Frame) -> None:
        """Follow other controllers' switching so ``toggle`` stays right."""
        a, b = frame.byte(0), frame.byte(1)
        if a < 0x80:
            key = f"A{a >> 1}"
        elif a < 0xA0:
            key = f"G{(a >> 1) & 0x0F}"
        elif a in (0xFE, 0xFF):
            key = "BC"
        else:
            return  # special commands do not switch lamps
        if not a & 1:
            self._on[key] = b not in (0, 0xFF) or (b == 0xFF and self._on.get(key, False))
        elif b == 0x00:
            self._on[key] = False
        elif b in (0x05, 0x06, 0x08) or 0x10 <= b <= 0x1F:
            self._on[key] = True
        if key == "BC":
            for k in list(self._on):
                self._on[k] = self._on["BC"]

    @staticmethod
    def _sensor_match(sel: dict[str, Any], ev: Event) -> bool:
        if "short_address" in sel and ev.short_address != sel["short_address"]:
            return False
        if "instance" in sel and ev.instance_number is not None and ev.instance_number != sel["instance"]:
            return False
        return True

    # -- actions -----------------------------------------------------------------
    def _send(self, frames: list[Frame]) -> None:
        if self.bus is not None:
            self.bus.submit(frames, source=self.name, priority=self.priority, multi_master=True)

    def _run(self, do: dict[str, Any], ev: Event) -> None:
        for action, arg in do.items():
            # YAML 1.1 reads a bare ``off:``/``on:`` key as a boolean.
            action = {False: "off", True: "recall_max"}.get(action, action)
            if action == "on":
                action = "recall_max"
            self.log.append(f"{action} {arg}")
            del self.log[:-200]
            if action == "toggle":
                key = str(arg).upper()
                a = parse_gear_target(key)
                on = self._on.get(key, False)
                self._send([Frame.forward16(a, 0x00 if on else 0x05)])
                self._on[key] = not on
            elif action in ("off", "recall_max", "recall_min", "up", "down", "on_and_step_up", "step_down_and_off",
                            "last_active"):
                op = {"off": 0x00, "recall_max": 0x05, "recall_min": 0x06, "up": 0x01, "down": 0x02,
                      "on_and_step_up": 0x08, "step_down_and_off": 0x07, "last_active": 0x0A}[action]
                key = str(arg).upper()
                self._send([Frame.forward16(parse_gear_target(key), op)])
                self._on[key] = action != "off"
            elif action == "scene":
                key = str(arg["target"]).upper()
                self._send([Frame.forward16(parse_gear_target(key), 0x10 | int(arg["scene"]))])
                self._on[key] = True
            elif action == "dapc":
                key = str(arg["target"]).upper()
                self._send([Frame.forward16(parse_gear_target(key) & 0xFE, int(arg["level"]))])
                self._on[key] = int(arg["level"]) > 0
            elif action == "dim_by_position":
                key = str(arg).upper()
                level = int(round(ev.info / 1023 * 254))
                self._send([Frame.forward16(parse_gear_target(key) & 0xFE, level)])
                self._on[key] = level > 0
            elif action == "daylight":
                for loop in self.daylight:
                    if loop.target.upper() == str(arg).upper():
                        loop.enabled = not loop.enabled

    def _daylight(self, loop: DaylightLoop, ev: Event) -> None:
        if not loop.enabled:
            return
        lux = ev.info / 1023 * loop.max_lux
        loop.measured = lux
        if not self._on.get(loop.target.upper(), False):
            return
        error = (loop.setpoint_lux - lux) / max(loop.setpoint_lux, 1)
        # Arc power levels are logarithmic: about 3 % light per step.
        step = int(round(loop.gain * error * 40))
        if step == 0:
            return
        loop.level = max(1, min(254, loop.level + step))
        self._send([Frame.forward16(parse_gear_target(loop.target) & 0xFE, loop.level)])

    def snapshot(self, t: float) -> dict[str, Any]:
        d = super().snapshot(t)
        d["kind"] = "app-controller"
        d["rules"] = [{"when": r.when, "do": r.do, "hits": r.hits} for r in self.rules]
        d["daylight"] = [{"target": dl.target, "setpoint_lux": dl.setpoint_lux, "measured_lux": round(dl.measured),
                          "level": dl.level, "enabled": dl.enabled} for dl in self.daylight]
        d["log"] = self.log[-20:]
        return d
