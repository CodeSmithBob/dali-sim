"""Build a :class:`~dali_sim.sim.Simulator` from a YAML (or dict) description.

See ``examples/*.yaml`` and ``docs/configuration.md`` for the format.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .automation import AutomationController
from .bus import Bus, Faults
from .clock import ManualClock, RealtimeClock
from .devices.control_device import ControlDevice
from .devices.instances import AbsoluteInput, Instance, LightSensor, OccupancySensor, PushButton
from .gear.colour import ColourExtension
from .gear.control_gear import ControlGear, LightSource
from .gear.emergency import EmergencyExtension, EmergencyGear
from .gear.led import LedExtension
from .sim import Simulator
from .world import Room, World


class ConfigError(ValueError):
    pass


def load(path: str | Path, *, manual_clock: bool = False) -> Simulator:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return build(data, manual_clock=manual_clock)


def _expand(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand ``count:`` entries into individual unit specs."""
    out = []
    for spec in items or []:
        count = int(spec.get("count", 1))
        if count == 1 and "count" not in spec:
            out.append(dict(spec))
            continue
        start = spec.get("start_address")
        for n in range(count):
            s = dict(spec)
            s.pop("count", None)
            s.pop("start_address", None)
            s["name"] = str(spec.get("name", "unit {n}")).format(n=n + 1, i=n)
            if isinstance(spec.get("room"), str):
                s["room"] = spec["room"].format(n=n + 1, i=n)
            if start is not None:
                s["short_address"] = int(start) + n
            out.append(s)
    return out


def make_gear(spec: dict[str, Any], seed: int | None) -> ControlGear:
    kind = str(spec.get("type", "led")).lower()
    common = dict(
        short_address=spec.get("short_address"),
        physical_min_level=int(spec.get("physical_min_level", 1)),
        power_watts=float(spec.get("power_w", 20.0)),
        seed=seed,
        dali_version=int(spec.get("dali_version", 2)),
    )
    name = spec["name"]
    if kind in ("led", "dt6"):
        g = ControlGear(name, extensions=[LedExtension()], **common)
    elif kind in ("plain", "dt0", "generic"):
        g = ControlGear(name, light_source=LightSource.LOW_PRESSURE_FLUORESCENT, **common)
    elif kind in ("relay", "switch", "on-off"):
        common["physical_min_level"] = 254
        g = ControlGear(name, light_source=LightSource.OTHER, **common)
    elif kind in ("tunable-white", "colour-tc", "tc"):
        g = ControlGear(name, extensions=[LedExtension(), ColourExtension(
            tc=True, tc_coolest_k=float(spec.get("coolest_k", 6500)), tc_warmest_k=float(spec.get("warmest_k", 2700)),
            auto_activation=bool(spec.get("auto_activation", False)))], **common)
    elif kind in ("rgb", "rgbw", "rgbwaf", "colour-rgbw"):
        n = {"rgb": 3, "rgbw": 4, "colour-rgbw": 4}.get(kind, int(spec.get("channels", 6)))
        g = ControlGear(name, extensions=[LedExtension(), ColourExtension(tc=False, rgbwaf_channels=n)], **common)
    elif kind in ("xy", "colour-xy"):
        g = ControlGear(name, extensions=[LedExtension(), ColourExtension(tc=False, xy=True)], **common)
    elif kind in ("emergency", "dt1"):
        g = EmergencyGear(name, emergency=EmergencyExtension(
            rated_duration_min=int(spec.get("rated_duration_min", 180)),
            emergency_level=int(spec.get("emergency_level", 254))), **common)
    else:
        raise ConfigError(f"unknown gear type {kind!r} for {name!r}")
    for grp in spec.get("groups", []) or []:
        g.groups |= 1 << int(grp)
    for scene, level in (spec.get("scenes") or {}).items():
        g.scenes[int(scene)] = int(level)
    for key in ("fade_time", "fade_rate", "min_level", "max_level", "power_on_level", "system_failure_level"):
        if key in spec:
            setattr(g, key, int(spec[key]))
    if "level" in spec:
        lvl = int(spec["level"])
        g._set_level(0.0, lvl)
    return g


def make_instance(spec: dict[str, Any] | str) -> Instance:
    if isinstance(spec, str):
        spec = {"type": spec}
    kind = str(spec.get("type", "push_button")).lower().replace("-", "_")
    label = str(spec.get("label", ""))
    if kind in ("push_button", "button", "301"):
        inst: Instance = PushButton(label, double_timer=int(spec.get("double_timer", 0)))
    elif kind in ("absolute_input", "slider", "rotary", "302"):
        inst = AbsoluteInput(label, resolution=int(spec.get("resolution", 8)), switch=bool(spec.get("switch", False)))
    elif kind in ("occupancy", "occupancy_sensor", "pir", "303"):
        hold_s = float(spec.get("hold_s", 900))
        inst = OccupancySensor(label, hold_timer=max(1, int(round(hold_s / 10))),
                               report_timer=int(spec.get("report_s", 20)))
    elif kind in ("light", "light_sensor", "lux", "304"):
        inst = LightSensor(label, max_lux=float(spec.get("max_lux", 2000)), resolution=int(spec.get("resolution", 10)),
                           report_timer=int(spec.get("report_s", 30)))
    else:
        raise ConfigError(f"unknown instance type {kind!r}")
    if "event_scheme" in spec:
        inst.event_scheme = int(spec["event_scheme"])
    if "priority" in spec:
        inst.event_priority = int(spec["priority"])
    if "group" in spec:
        inst.primary_group = int(spec["group"])
    return inst


def make_device(spec: dict[str, Any], seed: int | None) -> ControlDevice:
    d = ControlDevice(spec["name"], short_address=spec.get("short_address"), seed=seed,
                      instances=[make_instance(i) for i in spec.get("instances", [])])
    scheme = spec.get("event_scheme")
    for inst in d.instances:
        if scheme is not None and inst.event_scheme == 0:
            inst.event_scheme = int(scheme)
    for grp in spec.get("groups", []) or []:
        d.device_groups |= 1 << int(grp)
    return d


def build(data: dict[str, Any], *, manual_clock: bool = False) -> Simulator:
    seed = data.get("seed")
    bus_cfg = data.get("bus", {}) or {}
    faults = Faults(**(bus_cfg.get("faults") or {}), seed=seed)
    clock = ManualClock() if manual_clock else RealtimeClock(float(data.get("speed", 1.0)))
    bus = Bus(clock, name=str(bus_cfg.get("name", "bus0")), faults=faults)
    wcfg = data.get("world", {}) or {}
    world = World(outdoor_lux=float(wcfg.get("outdoor_lux", 10000)), people=bool(wcfg.get("people", False)),
                  daylight_cycle=bool(wcfg.get("daylight_cycle", False)), seed=seed)
    sim = Simulator(bus, world, name=str(data.get("name", "DALI bus")))
    sim.description = str(data.get("description", ""))
    addressed = bool(data.get("commissioned", True))
    rooms: dict[str, Room] = {}
    for r in data.get("rooms", []) or []:
        room = Room(name=r["name"], x=r.get("x", 0), y=r.get("y", 0), w=r.get("w", 4), h=r.get("h", 3),
                    daylight_factor=float(r.get("daylight_factor", 0.03)),
                    lux_per_lamp=float(r.get("lux_per_lamp", 150)))
        rooms[room.name] = world.add_room(room)

    n = 0
    for spec in _expand(data.get("gear", [])):
        if "initial_level" in data and "level" not in spec:
            spec["level"] = data["initial_level"]
        if not addressed:
            spec.pop("short_address", None)
        g = make_gear(spec, None if seed is None else seed * 1000 + n)
        n += 1
        g.meta = {k: spec[k] for k in ("room", "label", "x", "y") if k in spec}
        sim.add(g)
        if "room" in spec:
            _room(rooms, world, spec["room"]).gear.append(g.name)
    for spec in _expand(data.get("devices", [])):
        if not addressed:
            spec.pop("short_address", None)
        d = make_device(spec, None if seed is None else seed * 1000 + n)
        n += 1
        d.meta = {k: spec[k] for k in ("room", "label", "x", "y") if k in spec}
        sim.add(d)
        if "room" in spec:
            room = _room(rooms, world, spec["room"])
            for idx, inst in enumerate(d.instances):
                if isinstance(inst, LightSensor):
                    room.light_sensors.append((d.name, idx))
                elif isinstance(inst, OccupancySensor):
                    room.occupancy_sensors.append((d.name, idx))
    auto = data.get("automation")
    if auto:
        ac = AutomationController(str(auto.get("name", "app-controller")), rules=auto.get("rules"),
                                  daylight=auto.get("daylight"), short_address=auto.get("short_address"),
                                  seed=None if seed is None else seed * 1000 + n)
        sim.add(ac)
    return sim


def _room(rooms: dict[str, Room], world: World, name: str) -> Room:
    if name not in rooms:
        rooms[name] = world.add_room(Room(name=name, x=len(rooms) * 4.2))
    return rooms[name]
