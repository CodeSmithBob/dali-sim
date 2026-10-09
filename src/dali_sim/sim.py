"""The simulator: a bus with its units, a world, and optional bridges to
hardware, plus the actions the web UI and API expose."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from .automation import AutomationController
from .bus import Bus
from .clock import RealtimeClock
from .common import Unit
from .devices.control_device import ControlDevice
from .devices.instances import Instance
from .gear.control_gear import ControlGear
from .world import World


class Simulator:
    def __init__(self, bus: Bus | None = None, world: World | None = None, name: str = "DALI bus") -> None:
        self.name = name
        self.bus = bus or Bus(RealtimeClock())
        self.world = world or World()
        self.units: dict[str, Unit] = {}
        self.bridges: list[Any] = []
        self.description = ""
        self._tasks: list[asyncio.Task] = []
        self._started = False
        self.on_change: list[Callable[[], None]] = []

    # -- building -------------------------------------------------------------
    def add(self, unit: Unit) -> Unit:
        if unit.name in self.units:
            raise ValueError(f"duplicate unit name {unit.name!r}")
        self.units[unit.name] = unit
        self.bus.attach(unit)
        return unit

    def remove(self, name: str) -> None:
        unit = self.units.pop(name)
        self.bus.detach(unit)

    @property
    def gear(self) -> list[ControlGear]:
        return [u for u in self.units.values() if isinstance(u, ControlGear)]

    @property
    def devices(self) -> list[ControlDevice]:
        return [u for u in self.units.values() if isinstance(u, ControlDevice)]

    def unit(self, name: str) -> Unit:
        try:
            return self.units[name]
        except KeyError:
            raise KeyError(f"no unit named {name!r}") from None

    def instance(self, device: str, index: int) -> Instance | None:
        d = self.units.get(device)
        if isinstance(d, ControlDevice) and 0 <= index < len(d.instances):
            return d.instances[index]
        return None

    # -- running --------------------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.world.start(self)
        self._tasks.append(asyncio.create_task(self.bus.run(), name="bus"))
        for br in self.bridges:
            await br.start()

    async def stop(self) -> None:
        for br in self.bridges:
            try:
                await br.stop()
            except Exception:
                pass
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._started = False

    @property
    def speed(self) -> float:
        clock = self.bus.clock
        return clock.speed if isinstance(clock, RealtimeClock) else 0.0

    @speed.setter
    def speed(self, value: float) -> None:
        clock = self.bus.clock
        if isinstance(clock, RealtimeClock):
            clock.speed = value
            self.bus._kick()

    # -- actions (physical world) -----------------------------------------------------
    def act(self, action: str, **kw: Any) -> dict[str, Any]:
        """Perform a UI/API action.  Returns a small result dict."""
        a = action.replace("-", "_")
        if a == "press":
            self._inst(kw).press()
        elif a == "release":
            self._inst(kw).release()
        elif a == "click":
            self._inst(kw).click(float(kw.get("hold", 0.1)))
        elif a == "motion":
            self._inst(kw).motion(bool(kw.get("moving", True)))
        elif a == "set_lux":
            self._inst(kw).set_lux(float(kw["lux"]))
        elif a == "set_value":
            inst = self._inst(kw)
            if "fraction" in kw:
                inst.set_fraction(float(kw["fraction"]))
            else:
                inst.set_value(int(kw["value"]))
        elif a == "outdoor_lux":
            self.world.outdoor_lux = float(kw["lux"])
        elif a == "people":
            self.world.people_enabled = bool(kw.get("enabled", True))
        elif a == "mains":
            self.unit(kw["unit"]).set_mains(bool(kw.get("on", True)))
        elif a == "bus_power":
            self.bus.set_power(bool(kw.get("on", True)))
        elif a == "lamp_failure":
            g = self.unit(kw["unit"])
            g.lamp_failure = bool(kw.get("on", True))
        elif a == "gear_failure":
            g = self.unit(kw["unit"])
            g.gear_failure = bool(kw.get("on", True))
        elif a == "speed":
            self.speed = float(kw["value"])
        elif a == "faults":
            for k in ("drop_forward", "drop_backward", "corrupt_backward"):
                if k in kw:
                    setattr(self.bus.faults, k, float(kw[k]))
        elif a == "clear_addresses":
            for u in self.units.values():
                if not isinstance(u, AutomationController):
                    u.short_address = 0xFF
        else:
            raise ValueError(f"unknown action {action!r}")
        for fn in self.on_change:
            fn()
        return {"ok": True}

    def _inst(self, kw: dict[str, Any]) -> Any:
        inst = self.instance(kw["device"], int(kw.get("instance", 0)))
        if inst is None:
            raise KeyError(f"no instance {kw.get('instance', 0)} on {kw['device']!r}")
        return inst

    # -- state --------------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        t = self.bus.now()
        gear = [g.snapshot(t) for g in self.gear]
        return {
            "name": self.name,
            "description": self.description,
            "time": round(t, 3),
            "speed": self.speed,
            "bus": {"powered": self.bus.powered, "stats": dict(self.bus.stats),
                    "faults": {k: getattr(self.bus.faults, k) for k in ("drop_forward", "drop_backward", "corrupt_backward")},
                    "power_w": round(sum(g["power_w"] for g in gear), 1),
                    "errors": len(self.bus.errors),
                    "last_error": self.bus.errors[-1].splitlines()[-1] if self.bus.errors else None},
            "gear": gear,
            "devices": [d.snapshot(t) for d in self.devices],
            "world": self.world.snapshot(t),
            "bridges": [br.describe() for br in self.bridges],
        }
