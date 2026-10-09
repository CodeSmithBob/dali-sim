"""A small physical world around the bus: rooms, daylight and people.

Light sensors read the daylight that falls into their room plus the light of
the lamps in that room, so a daylight-harvesting rule forms a real closed
loop.  Optional "people" walk in and out and trigger occupancy sensors.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .sim import Simulator


@dataclass
class Room:
    name: str
    x: float = 0
    y: float = 0
    w: float = 4
    h: float = 3
    daylight_factor: float = 0.03  # fraction of outdoor illuminance reaching the sensor
    lux_per_lamp: float = 150.0  # lux each lamp adds at 100 % on the work plane
    gear: list[str] = field(default_factory=list)
    light_sensors: list[tuple[str, int]] = field(default_factory=list)
    occupancy_sensors: list[tuple[str, int]] = field(default_factory=list)
    people: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "gear": self.gear, "people": self.people}


class World:
    def __init__(self, outdoor_lux: float = 10000.0, people: bool = False, daylight_cycle: bool = False,
                 seed: int | None = None, tick: float = 1.0) -> None:
        self.rooms: dict[str, Room] = {}
        self.outdoor_lux = outdoor_lux
        self.people_enabled = people
        self.daylight_cycle = daylight_cycle
        self.rng = random.Random(seed)
        self.tick = tick
        self.sim: Simulator | None = None
        self._timer: int | None = None

    def add_room(self, room: Room) -> Room:
        self.rooms[room.name] = room
        return room

    def room_illuminance(self, room: Room, t: float) -> float:
        sim = self.sim
        lamps = 0.0
        if sim is not None:
            for name in room.gear:
                g = sim.units.get(name)
                if g is not None and hasattr(g, "light_percent_at"):
                    lamps += g.light_percent_at(t) / 100 * room.lux_per_lamp
        return self.outdoor_lux * room.daylight_factor + lamps

    def start(self, sim: Simulator) -> None:
        self.sim = sim
        self._timer = sim.bus.call_later(self.tick, self._step)

    def _step(self, t: float) -> None:
        sim = self.sim
        if sim is None:
            return
        if self.daylight_cycle:
            # one simulated day = 24 h of simulation time, peak at noon
            hour = (t / 3600.0) % 24
            self.outdoor_lux = max(0.0, 20000 * math.sin(math.pi * (hour - 6) / 12)) if 6 <= hour <= 18 else 0.0
        for room in self.rooms.values():
            lux = self.room_illuminance(room, t)
            for dev, idx in room.light_sensors:
                inst = sim.instance(dev, idx)
                if inst is not None and abs(getattr(inst, "lux", 0.0) - lux) > 0.5:
                    inst.set_lux(lux)
            if self.people_enabled:
                self._people(room)
        self._timer = sim.bus.call_at(t + self.tick, self._step)

    def _people(self, room: Room) -> None:
        sim = self.sim
        before = room.people
        if room.people and self.rng.random() < 0.02:
            room.people -= 1
        elif self.rng.random() < 0.01:
            room.people += 1
        moving = room.people > 0 and self.rng.random() < 0.3
        if room.people != before or moving:
            for dev, idx in room.occupancy_sensors:
                inst = sim.instance(dev, idx)
                if inst is not None:
                    if room.people and (moving or before == 0):
                        inst.motion(True)
                    elif inst.movement:
                        inst.motion(False)

    def snapshot(self, t: float) -> dict[str, Any]:
        return {
            "outdoor_lux": round(self.outdoor_lux),
            "people": self.people_enabled,
            "rooms": [dict(r.to_dict(), lux=round(self.room_illuminance(r, t))) for r in self.rooms.values()],
        }
