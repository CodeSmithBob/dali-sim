"""A building: several independent DALI lines simulated together.

Each line is a full :class:`~dali_sim.sim.Simulator` with its own bus, units
and world; frames on one line never reach another, as with real DALI lines.
The lines share one real-time clock, so the speed control applies to all.

Line numbers are 1-based wherever users see them ("Line 1").
"""

from __future__ import annotations

from typing import Any

from .sim import Simulator


class Building:
    def __init__(self, lines: list[Simulator], name: str = "", description: str = "") -> None:
        if not lines:
            raise ValueError("a building needs at least one line")
        self.lines = lines
        self.name = name or lines[0].name
        self.description = description or (lines[0].description if len(lines) == 1 else "")

    def __len__(self) -> int:
        return len(self.lines)

    def line(self, number: int | str | None) -> Simulator:
        """Line by 1-based number (None means line 1)."""
        n = 1 if number in (None, "") else int(number)
        if not 1 <= n <= len(self.lines):
            raise ValueError(f"no line {n}: this building has {len(self.lines)} line(s)")
        return self.lines[n - 1]

    def pad_to(self, count: int) -> None:
        """Add empty lines so that servers created for ``count`` lines keep working."""
        from .config import build

        clock = self.lines[0].bus.clock
        while len(self.lines) < count:
            n = len(self.lines) + 1
            self.lines.append(build({"name": f"Line {n} (empty)"}, clock=clock))

    async def start(self) -> None:
        for sim in self.lines:
            await sim.start()

    async def stop(self) -> None:
        for sim in self.lines:
            await sim.stop()

    def summary(self) -> list[dict[str, Any]]:
        out = []
        for i, sim in enumerate(self.lines, 1):
            t = sim.bus.now()
            gear = sim.gear
            out.append({
                "line": i,
                "name": sim.name,
                "gear": len(gear),
                "devices": len(sim.devices),
                "lamps_on": sum(1 for g in gear if g.light_percent_at(t) > 0),
                "power_w": round(sum(g.power_watts * g.light_percent_at(t) / 100 for g in gear), 1),
                "frames": sim.bus.stats["forward"] + sim.bus.stats["events"],
                "powered": sim.bus.powered,
                "errors": len(sim.bus.errors),
            })
        return out
