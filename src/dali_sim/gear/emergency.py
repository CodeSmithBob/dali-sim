"""Device type 1: self-contained emergency lighting (IEC 62386-202).

The model is a *maintained* emergency luminaire: with mains present the lamp
follows normal part 102 arc power commands; when mains fails it switches to
the emergency level and runs from its battery until the battery is empty.
The DALI interface stays on line (it is powered from the bus).

Function and duration tests, inhibit and rest mode are simulated.  Times run
on simulation time, so speed the clock up to watch a full duration test.
"""

from __future__ import annotations

from typing import Any

from ..common import MASK, YES
from ..memory import version_byte
from ..protocol.commands import DT1_COMMANDS
from .control_gear import ControlGear, GearExtension

FUNCTION_TEST_SECONDS = 30.0


class EmergencyExtension(GearExtension):
    device_type = 1
    version = version_byte(2, 0)
    commands = DT1_COMMANDS

    # emergency mode bits
    REST, NORMAL, EMERGENCY, EXTENDED, FT_RUNNING, DT_RUNNING, HW_INHIBIT, HW_SWITCH = range(8)

    def __init__(self, rated_duration_min: int = 180, battery_charge: float = 1.0,
                 emergency_level: int = 254, emergency_min: int = 1, emergency_max: int = 254) -> None:
        super().__init__()
        self.rated_duration = rated_duration_min * 60.0
        self.battery = battery_charge  # 0..1
        self.emergency_level = emergency_level
        self.emergency_min = emergency_min
        self.emergency_max = emergency_max
        self.in_emergency = False
        self.rest_mode = False
        self.inhibit_until = -1.0
        self.function_test: tuple[float, float] | None = None  # (start, end)
        self.duration_test_start: float | None = None
        self.ft_done = False
        self.dt_done = False
        self.ft_failed = False
        self.dt_failed = False
        self.dt_result = 0  # in units of 2 minutes
        self.ft_pending = False
        self.dt_pending = False
        self.lamp_emergency_time = 0.0  # seconds
        self.lamp_total_time = 0.0
        self.identify_until = -1.0
        self.function_interval_days = 7
        self.duration_interval_weeks = 52
        self.test_timeout_days = 7
        self.prolong_time = 0  # 30 s units
        self.test_delay = 0  # 15 min units
        self.battery_failure = False
        self.lamp_failure = False
        self._last_t = 0.0

    # ------------------------------------------------------------------
    def tick(self, t: float) -> None:
        dt = max(0.0, t - self._last_t)
        self._last_t = t
        g = self.gear
        if g is None or dt == 0:
            return
        lamp_on_battery = self.in_emergency and not self.rest_mode
        testing = self.function_test is not None or self.duration_test_start is not None
        if lamp_on_battery or testing:
            self.battery = max(0.0, self.battery - dt / self.rated_duration)
            self.lamp_emergency_time += dt
            if self.battery == 0.0 and self.duration_test_start is not None:
                elapsed = t - self.duration_test_start
                self.dt_result = min(254, int(elapsed // 120))
                self.dt_failed = elapsed < self.rated_duration
                self.dt_done = True
                self.duration_test_start = None
        elif g.mains:
            # recharge in 1/10 of the rated duration per 10 % (24 h for 3 h units is typical;
            # we charge faster so the demo stays watchable)
            self.battery = min(1.0, self.battery + dt / (self.rated_duration * 2))
        if g.level_at(t) > 0 or lamp_on_battery:
            self.lamp_total_time += dt
        if self.function_test is not None and t >= self.function_test[1]:
            self.ft_done = True
            self.ft_failed = self.lamp_failure or self.battery_failure or self.battery <= 0.0
            self.function_test = None
        if self.duration_test_start is not None and t - self.duration_test_start >= self.rated_duration:
            self.dt_result = min(254, int(self.rated_duration // 120))
            self.dt_failed = False
            self.dt_done = True
            self.duration_test_start = None

    def mains_changed(self, on: bool, t: float) -> None:
        self.tick(t)
        if on:
            self.in_emergency = False
            self.rest_mode = False
        else:
            self.in_emergency = True
            self.rest_mode = t < self.inhibit_until

    def emergency_lamp_percent(self) -> float | None:
        """Light output while running from the battery, or None."""
        testing = self.function_test is not None or self.duration_test_start is not None
        if (self.in_emergency and not self.rest_mode or testing) and self.battery > 0 and not self.lamp_failure:
            from ..protocol.dimming import log_curve_percent
            return log_curve_percent(self.emergency_level)
        return None

    def mode_byte(self) -> int:
        bits = {
            self.REST: self.rest_mode,
            self.NORMAL: not self.in_emergency and self.function_test is None and self.duration_test_start is None,
            self.EMERGENCY: self.in_emergency and not self.rest_mode,
            self.FT_RUNNING: self.function_test is not None,
            self.DT_RUNNING: self.duration_test_start is not None,
        }
        return sum(int(v) << k for k, v in bits.items())

    def status_byte(self, t: float) -> int:
        bits = [t < self.inhibit_until, self.ft_done, self.dt_done, self.battery >= 0.999,
                self.ft_pending, self.dt_pending, t < self.identify_until, False]
        return sum(int(b) << i for i, b in enumerate(bits))

    def failure_byte(self) -> int:
        bits = [False, self.dt_failed and self.dt_done, self.battery_failure, self.lamp_failure,
                False, False, self.ft_failed, self.dt_failed]
        return sum(int(b) << i for i, b in enumerate(bits))

    FEATURES = 0b0011_1011  # integral, maintained, auto test, adjustable level, hw inhibit

    def command(self, opcode: int, t: float, confirmed: bool) -> int | None:  # noqa: C901
        g = self.gear
        assert g is not None
        self.tick(t)
        if opcode == 0xE0:  # REST
            if self.in_emergency:
                self.rest_mode = True
        elif opcode == 0xE1:  # INHIBIT (15 minutes)
            self.inhibit_until = t + 15 * 60
        elif opcode == 0xE2:  # RE-LIGHT/RESET INHIBIT
            self.rest_mode = False
            self.inhibit_until = -1.0
        elif opcode == 0xE3:  # START FUNCTION TEST
            self.function_test = (t, t + FUNCTION_TEST_SECONDS)
            self.ft_pending = False
        elif opcode == 0xE4:  # START DURATION TEST
            if self.battery >= 0.999:
                self.duration_test_start = t
                self.dt_pending = False
            else:
                self.dt_pending = True
        elif opcode == 0xE5:  # STOP TEST
            self.function_test = None
            self.duration_test_start = None
            self.ft_pending = self.dt_pending = False
        elif opcode == 0xE6:
            self.ft_done = False
        elif opcode == 0xE7:
            self.dt_done = False
        elif opcode == 0xE8:
            self.lamp_emergency_time = 0.0
            self.lamp_total_time = 0.0
        elif opcode == 0xE9 and confirmed:
            self.emergency_level = min(max(g.dtr0, self.emergency_min), self.emergency_max)
        elif opcode == 0xEA and confirmed:
            self.test_delay = (g.dtr0 << 8) | (self.test_delay & 0xFF)
        elif opcode == 0xEB and confirmed:
            self.test_delay = (self.test_delay & 0xFF00) | g.dtr0
        elif opcode == 0xEC and confirmed:
            self.function_interval_days = g.dtr0
        elif opcode == 0xED and confirmed:
            self.duration_interval_weeks = g.dtr0
        elif opcode == 0xEE and confirmed:
            self.test_timeout_days = g.dtr0
        elif opcode == 0xEF and confirmed:
            self.prolong_time = g.dtr0
        elif opcode == 0xF0:  # START IDENTIFICATION
            self.identify_until = t + 10.0
            g._identify(t)
        elif opcode == 0xF1:
            return int(round(self.battery * 254))
        elif opcode == 0xF2:  # QUERY TEST TIMING (selected by DTR0)
            return {
                4: self.function_interval_days, 5: self.duration_interval_weeks,
                6: self.test_timeout_days, 7: self.prolong_time,
            }.get(g.dtr0, 0)
        elif opcode == 0xF3:
            return self.dt_result
        elif opcode == 0xF4:
            return min(254, int(self.lamp_emergency_time // 3600))
        elif opcode == 0xF5:
            return min(254, int(self.lamp_total_time // (4 * 3600)))
        elif opcode == 0xF6:
            return self.emergency_level
        elif opcode == 0xF7:
            return self.emergency_min
        elif opcode == 0xF8:
            return self.emergency_max
        elif opcode == 0xF9:
            return min(254, int(self.rated_duration // 120))
        elif opcode == 0xFA:
            return self.mode_byte()
        elif opcode == 0xFB:
            return self.FEATURES
        elif opcode == 0xFC:
            return self.failure_byte()
        elif opcode == 0xFD:
            return self.status_byte(t)
        elif opcode == 0xFE:  # PERFORM DTR SELECTED FUNCTION
            if g.dtr0 == 0:
                self.emergency_level = self.emergency_max
                self.inhibit_until = -1.0
        elif opcode == 0xFF:
            return self.version
        return None

    def state(self, t: float) -> dict[str, Any]:
        self.tick(t)
        return {"dt1": {
            "battery": round(self.battery * 100, 1),
            "emergency": self.in_emergency,
            "rest": self.rest_mode,
            "function_test": self.function_test is not None,
            "duration_test": self.duration_test_start is not None,
            "ft_failed": self.ft_failed,
            "dt_failed": self.dt_failed,
            "dt_result_min": self.dt_result * 2,
            "mode": self.mode_byte(),
        }}


class EmergencyGear(ControlGear):
    """Control gear with an :class:`EmergencyExtension`.  Stays on the bus
    when its mains supply fails."""

    def __init__(self, name: str, *, emergency: EmergencyExtension | None = None, **kw: Any) -> None:
        self.emergency = emergency or EmergencyExtension()
        exts = list(kw.pop("extensions", []) or [])
        super().__init__(name, extensions=[self.emergency, *exts], **kw)

    @property
    def online(self) -> bool:
        return self.bus is None or self.bus.powered

    def set_mains(self, on: bool) -> None:
        t = self.bus.now() if self.bus else 0.0
        super().set_mains(on)
        self.emergency.mains_changed(on, t)

    def light_percent_at(self, t: float) -> float:
        self.emergency.tick(t)
        p = self.emergency.emergency_lamp_percent()
        if p is not None:
            return p
        return super().light_percent_at(t)

    def level_at(self, t: float) -> int:
        lvl = super().level_at(t)
        if self.emergency.emergency_lamp_percent() is not None:
            return self.emergency.emergency_level
        return lvl


__all__ = ["EmergencyExtension", "EmergencyGear", "MASK", "YES"]
