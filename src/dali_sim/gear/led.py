"""Device type 6: LED modules (IEC 62386-207)."""

from __future__ import annotations

from typing import Any

from ..common import YES
from ..memory import version_byte
from ..protocol.commands import DT6_COMMANDS
from ..protocol.dimming import linear_curve_percent
from .control_gear import GearExtension


class LedExtension(GearExtension):
    device_type = 6
    version = version_byte(2, 0)
    commands = DT6_COMMANDS

    #: QUERY GEAR TYPE bits
    GEAR_TYPE_INTEGRATED_PSU = 1 << 0
    GEAR_TYPE_INTEGRATED_MODULE = 1 << 1
    GEAR_TYPE_AC = 1 << 2
    GEAR_TYPE_DC = 1 << 3

    #: QUERY FAILURE STATUS / QUERY FEATURES bit positions
    SHORT_CIRCUIT, OPEN_CIRCUIT, LOAD_DECREASE, LOAD_INCREASE = 0, 1, 2, 3
    CURRENT_PROTECTOR, THERMAL_SHUTDOWN, THERMAL_OVERLOAD, REFERENCE_FAILED = 4, 5, 6, 7

    def __init__(self, gear_type: int = GEAR_TYPE_INTEGRATED_PSU | GEAR_TYPE_AC,
                 operating_modes: int = 0b0100, features: int = 0b0110_0011,
                 min_fast_fade: int = 0) -> None:
        super().__init__()
        self.gear_type = gear_type
        self.possible_modes = operating_modes  # bit0 PWM, bit1 AM, bit2 current, bit3 high current pulse
        self.features = features
        self.dimming_curve = 0  # 0 = standard (log), 1 = linear
        self.failures = 0
        self.fast_fade_time = 0
        self.min_fast_fade_time = min_fast_fade
        self.operating_mode = 0b0100

    # failures that the UI can inject --------------------------------------
    def set_failure(self, bit: int, active: bool) -> None:
        if active:
            self.failures |= 1 << bit
        else:
            self.failures &= ~(1 << bit)
        g = self.gear
        if g is not None:
            g.lamp_failure = bool(self.failures & 0b11)
            g.gear_failure = bool(self.failures & 0b1111_0000)

    def light_percent(self, level: int) -> float | None:
        if self.dimming_curve == 1:
            return linear_curve_percent(level)
        return None

    def on_reset(self) -> None:
        self.dimming_curve = 0
        self.fast_fade_time = 0

    def command(self, opcode: int, t: float, confirmed: bool) -> int | None:
        g = self.gear
        assert g is not None
        if opcode == 0xE3:  # SELECT DIMMING CURVE
            if g.dtr0 in (0, 1):
                self.dimming_curve = g.dtr0
        elif opcode == 0xE4:  # SET FAST FADE TIME
            v = g.dtr0
            if v == 0 or self.min_fast_fade_time <= v <= 27:
                self.fast_fade_time = v
        elif opcode == 0xED:
            return self.gear_type
        elif opcode == 0xEE:
            return self.dimming_curve
        elif opcode == 0xEF:
            return self.possible_modes
        elif opcode == 0xF0:
            return self.features
        elif opcode == 0xF1:
            return self.failures
        elif 0xF2 <= opcode <= 0xF8:
            bit = opcode - 0xF2
            return YES if self.failures & (1 << bit) else None
        elif opcode == 0xF9:
            return None  # reference measurement never running
        elif opcode == 0xFA:
            return YES if self.failures & (1 << self.REFERENCE_FAILED) else None
        elif opcode == 0xFB:
            return YES if self.features & (1 << 4) else None
        elif opcode == 0xFC:
            return self.operating_mode
        elif opcode == 0xFD:
            return self.fast_fade_time
        elif opcode == 0xFE:
            return self.min_fast_fade_time
        elif opcode == 0xFF:
            return self.version
        return None

    def state(self, t: float) -> dict[str, Any]:
        return {"dt6": {"dimming_curve": "linear" if self.dimming_curve else "logarithmic",
                        "failures": self.failures}}
