"""Device type 8: colour control (IEC 62386-209).

Supported colour types: colour temperature Tc (tunable white), CIE 1931 xy
coordinates, and RGBWAF channels (up to six: red, green, blue, white, amber,
free colour).  Values are first written as *temporary* values and take effect
on ACTIVATE (or with the next arc power command when automatic activation is
on).  A change fades over the gear's fade time.
"""

from __future__ import annotations

import math
from typing import Any

from ..common import MASK
from ..memory import version_byte
from ..protocol.commands import DT8_COMMANDS
from ..protocol.dimming import mirek_to_kelvin
from .control_gear import GearExtension

MASK16 = 0xFFFF

XY, TC, PRIMARY, RGBWAF = 0x10, 0x20, 0x40, 0x80
TYPE_NAMES = {XY: "xy", TC: "tc", PRIMARY: "primary-n", RGBWAF: "rgbwaf"}


class ColourExtension(GearExtension):
    device_type = 8
    version = version_byte(2, 0)
    commands = DT8_COMMANDS

    def __init__(self, *, tc: bool = True, xy: bool = False, rgbwaf_channels: int = 0, primaries: int = 0,
                 tc_coolest_k: float = 6500, tc_warmest_k: float = 2700, auto_activation: bool = False) -> None:
        super().__init__()
        if not (tc or xy or rgbwaf_channels or primaries):
            raise ValueError("a colour gear needs at least one colour type")
        self.tc_capable = tc
        self.xy_capable = xy
        self.rgbwaf_channels = max(0, min(6, rgbwaf_channels))
        self.primaries = max(0, min(6, primaries))
        self.phys_coolest = int(round(1e6 / tc_coolest_k))
        self.phys_warmest = int(round(1e6 / tc_warmest_k))
        self.coolest = self.phys_coolest
        self.warmest = self.phys_warmest
        self.auto_activation = auto_activation
        # actual colour
        self.colour_type = TC if tc else XY if xy else RGBWAF if rgbwaf_channels else PRIMARY
        self.x = 0x5555  # ~0.333
        self.y = 0x5555
        self.tc = (self.coolest + self.warmest) // 2
        self.rgbwaf = [254] * 6
        self.primary_levels = [0] * 6
        self.rgbwaf_control = (1 << self.rgbwaf_channels) - 1  # all channels, control type 0
        # temporary colour
        self._clear_temporary()
        # fading
        self._from: dict[str, Any] | None = None
        self._t0 = 0.0
        self._t1 = 0.0
        # scenes store colour too
        self.scene_colour: list[dict[str, Any] | None] = [None] * 16
        self.calibrating = False
        self.calibration_ok = False

    # ------------------------------------------------------------------
    def _clear_temporary(self) -> None:
        self.t_type: int | None = None
        self.t_x = MASK16
        self.t_y = MASK16
        self.t_tc = MASK16
        self.t_rgbwaf = [MASK] * 6
        self.t_primary = [MASK16] * 6

    def _current(self) -> dict[str, Any]:
        return {"type": self.colour_type, "x": self.x, "y": self.y, "tc": self.tc,
                "rgbwaf": list(self.rgbwaf), "primary": list(self.primary_levels)}

    def _temporary(self) -> dict[str, Any] | None:
        if self.t_type is None:
            return None
        cur = self._current()
        cur["type"] = self.t_type
        if self.t_type == XY:
            cur["x"] = self.x if self.t_x == MASK16 else self.t_x
            cur["y"] = self.y if self.t_y == MASK16 else self.t_y
        elif self.t_type == TC:
            if self.t_tc != MASK16:
                cur["tc"] = min(max(self.t_tc, self.coolest), self.warmest)
        elif self.t_type == RGBWAF:
            cur["rgbwaf"] = [c if t == MASK else t for c, t in zip(self.rgbwaf, self.t_rgbwaf)]
        elif self.t_type == PRIMARY:
            cur["primary"] = [c if t == MASK16 else t for c, t in zip(self.primary_levels, self.t_primary)]
        return cur

    def _apply(self, colour: dict[str, Any], t: float, duration: float) -> None:
        self._from = self._colour_at(t)
        self._t0, self._t1 = t, t + max(0.0, duration)
        self.colour_type = colour["type"]
        self.x, self.y, self.tc = colour["x"], colour["y"], colour["tc"]
        self.rgbwaf = list(colour["rgbwaf"])
        self.primary_levels = list(colour["primary"])

    def _activate(self, t: float, duration: float | None = None) -> None:
        temp = self._temporary()
        if temp is None:
            return
        g = self.gear
        if duration is None:
            duration = g.effective_fade_time() if g else 0.0
        self._apply(temp, t, duration)
        self._clear_temporary()

    def _colour_at(self, t: float) -> dict[str, Any]:
        cur = self._current()
        if self._from is None or t >= self._t1 or self._t1 <= self._t0:
            return cur
        f = (t - self._t0) / (self._t1 - self._t0)
        a = self._from
        mix = dict(cur)
        for k in ("x", "y", "tc"):
            mix[k] = a[k] + (cur[k] - a[k]) * f
        mix["rgbwaf"] = [p + (q - p) * f for p, q in zip(a["rgbwaf"], cur["rgbwaf"])]
        return mix

    # hooks -----------------------------------------------------------------
    def on_arc_power(self, t: float, duration: float, scene: int | None = None) -> None:
        if scene is not None and self.scene_colour[scene] is not None:
            self._apply(self.scene_colour[scene], t, duration)
            self._clear_temporary()
        elif self.auto_activation:
            self._activate(t, duration)

    def on_store_scene(self, scene: int) -> None:
        self.scene_colour[scene] = self._temporary() or self._current()

    def on_remove_scene(self, scene: int) -> None:
        self.scene_colour[scene] = None

    def on_reset(self) -> None:
        self.coolest, self.warmest = self.phys_coolest, self.phys_warmest
        self.colour_type = TC if self.tc_capable else self.colour_type
        self.tc = (self.coolest + self.warmest) // 2
        self.rgbwaf = [254] * 6
        self._clear_temporary()
        self.scene_colour = [None] * 16
        self.auto_activation = False

    # commands --------------------------------------------------------------
    def _dtr16(self) -> int:
        g = self.gear
        return (g.dtr1 << 8) | g.dtr0

    def colour_type_features(self) -> int:
        return (int(self.xy_capable) | (int(self.tc_capable) << 1) | (self.primaries << 2)
                | (self.rgbwaf_channels << 5))

    def colour_status(self) -> int:
        return (
            int(self.calibrating) << 2 | int(self.calibration_ok) << 3
            | (1 << {XY: 4, TC: 5, PRIMARY: 6, RGBWAF: 7}[self.colour_type])
        )

    def command(self, opcode: int, t: float, confirmed: bool) -> int | None:  # noqa: C901
        g = self.gear
        assert g is not None
        if opcode == 0xE0 and self.xy_capable:
            self.t_x, self.t_type = self._dtr16(), XY
        elif opcode == 0xE1 and self.xy_capable:
            self.t_y, self.t_type = self._dtr16(), XY
        elif opcode == 0xE2:  # ACTIVATE
            self._activate(t)
        elif opcode in (0xE3, 0xE4, 0xE5, 0xE6) and self.colour_type == XY:
            step = 256  # one step = 256 units of the 16-bit coordinate
            if opcode == 0xE3:
                self.x = min(self.x + step, 0xFFFE)
            elif opcode == 0xE4:
                self.x = max(self.x - step, 0)
            elif opcode == 0xE5:
                self.y = min(self.y + step, 0xFFFE)
            else:
                self.y = max(self.y - step, 0)
            self._from = None
        elif opcode == 0xE7 and self.tc_capable:
            self.t_tc, self.t_type = self._dtr16(), TC
        elif opcode == 0xE8 and self.colour_type == TC:  # STEP COOLER
            self.tc = max(self.tc - 1, self.coolest)
            self._from = None
        elif opcode == 0xE9 and self.colour_type == TC:  # STEP WARMER
            self.tc = min(self.tc + 1, self.warmest)
            self._from = None
        elif opcode == 0xEA and self.primaries:
            n = g.dtr2
            if n < self.primaries:
                self.t_primary[n] = self._dtr16()
                self.t_type = PRIMARY
        elif opcode == 0xEB and self.rgbwaf_channels:
            self.t_rgbwaf[0:3] = [g.dtr0, g.dtr1, g.dtr2]
            self.t_type = RGBWAF
        elif opcode == 0xEC and self.rgbwaf_channels:
            self.t_rgbwaf[3:6] = [g.dtr0, g.dtr1, g.dtr2]
            self.t_type = RGBWAF
        elif opcode == 0xED and self.rgbwaf_channels:
            self.rgbwaf_control = g.dtr0
        elif opcode == 0xEE:  # COPY REPORT TO TEMPORARY
            self.t_type = self.colour_type
            self.t_x, self.t_y, self.t_tc = self.x, self.y, self.tc
            self.t_rgbwaf = list(self.rgbwaf)
            self.t_primary = list(self.primary_levels)
        elif opcode == 0xF2 and confirmed:  # STORE COLOUR TEMPERATURE TC LIMIT
            v = self._dtr16()
            which = g.dtr2
            if which == 0:
                self.coolest = min(max(v, self.phys_coolest), self.warmest)
            elif which == 1:
                self.warmest = max(min(v, self.phys_warmest), self.coolest)
            elif which == 2:
                self.phys_coolest = v
            elif which == 3:
                self.phys_warmest = v
        elif opcode == 0xF3 and confirmed:  # STORE GEAR FEATURES/STATUS
            self.auto_activation = bool(g.dtr0 & 1)
        elif opcode == 0xF6 and confirmed:  # START AUTO CALIBRATION
            self.calibrating = False
            self.calibration_ok = True
        elif opcode in (0xF0, 0xF1, 0xF5):
            pass  # stored but not modelled further
        elif opcode == 0xF7:
            return int(self.auto_activation)
        elif opcode == 0xF8:
            return self.colour_status()
        elif opcode == 0xF9:
            return self.colour_type_features()
        elif opcode == 0xFA:
            return self._query_colour_value(g.dtr0)
        elif opcode == 0xFB:
            return self.rgbwaf_control if self.rgbwaf_channels else MASK
        elif opcode == 0xFC:
            return MASK
        elif opcode == 0xFF:
            return self.version
        return None

    def _answer16(self, value: int) -> int:
        g = self.gear
        g.dtr0 = value & 0xFF
        return (value >> 8) & 0xFF

    def _query_colour_value(self, sel: int) -> int:  # noqa: C901
        g = self.gear
        cur = self._current()
        temp = self._temporary() if self.t_type is not None else None

        def values(src: dict[str, Any] | None, base: int) -> int | None:
            off = sel - base
            if src is None:
                return self._answer16(MASK16) if off <= 2 else MASK
            if off == 0:
                return self._answer16(int(src["x"]) if self.xy_capable else MASK16)
            if off == 1:
                return self._answer16(int(src["y"]) if self.xy_capable else MASK16)
            if off == 2:
                return self._answer16(int(src["tc"]) if self.tc_capable else MASK16)
            if 3 <= off <= 8:
                n = off - 3
                return self._answer16(int(src["primary"][n]) if n < self.primaries else MASK16)
            if 9 <= off <= 14:
                n = off - 9
                return int(src["rgbwaf"][n]) if n < self.rgbwaf_channels else MASK
            if off == 15:
                return src["type"]
            return None

        if 0 <= sel <= 15:
            return values(cur, 0)
        if sel == 128:
            return self._answer16(self.coolest if self.tc_capable else MASK16)
        if sel == 129:
            return self._answer16(self.phys_coolest if self.tc_capable else MASK16)
        if sel == 130:
            return self._answer16(self.warmest if self.tc_capable else MASK16)
        if sel == 131:
            return self._answer16(self.phys_warmest if self.tc_capable else MASK16)
        if 192 <= sel <= 207:
            v = values(temp, 192)
            return MASK if v is None else v
        if 208 <= sel <= 223:
            v = values(cur, 208)
            return MASK if v is None else v
        g.dtr0 = MASK
        return MASK

    # visualisation ------------------------------------------------------------
    def rgb_at(self, t: float) -> tuple[int, int, int]:
        c = self._colour_at(t)
        if c["type"] == TC:
            return kelvin_to_rgb(mirek_to_kelvin(int(c["tc"])) if c["tc"] else 4000)
        if c["type"] == XY:
            return xy_to_rgb(c["x"] / 65536, c["y"] / 65536)
        if c["type"] == RGBWAF:
            ch = [v / 254 for v in c["rgbwaf"]] + [0.0] * 6
            n = self.rgbwaf_channels
            r, gg, b = (ch[0], ch[1], ch[2]) if n >= 3 else (1.0, 1.0, 1.0)
            w = ch[3] if n >= 4 else 0.0
            a = ch[4] if n >= 5 else 0.0
            r, gg, b = r + w + a, gg + w + 0.6 * a, b + w
            m = max(r, gg, b, 1e-6)
            return int(255 * r / m), int(255 * gg / m), int(255 * b / m)
        return (255, 255, 255)

    def state(self, t: float) -> dict[str, Any]:
        c = self._colour_at(t)
        r, g, b = self.rgb_at(t)
        return {"dt8": {
            "colour_type": TYPE_NAMES.get(c["type"], "?"),
            "tc_mirek": int(c["tc"]),
            "tc_kelvin": round(mirek_to_kelvin(int(c["tc"]))) if c["tc"] else None,
            "x": round(c["x"] / 65536, 4),
            "y": round(c["y"] / 65536, 4),
            "rgbwaf": [int(v) for v in c["rgbwaf"][: self.rgbwaf_channels]],
            "rgb": f"#{r:02x}{g:02x}{b:02x}",
        }}


def kelvin_to_rgb(kelvin: float) -> tuple[int, int, int]:
    """Approximate sRGB of a black body at ``kelvin`` (for display only)."""
    k = max(1000.0, min(40000.0, kelvin)) / 100
    if k <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(k) - 161.1195681661
        b = 0.0 if k <= 19 else 138.5177312231 * math.log(k - 10) - 305.0447927307
    else:
        r = 329.698727446 * (k - 60) ** -0.1332047592
        g = 288.1221695283 * (k - 60) ** -0.0755148492
        b = 255.0
    clamp = lambda v: int(max(0, min(255, v)))  # noqa: E731
    return clamp(r), clamp(g), clamp(b)


def xy_to_rgb(x: float, y: float) -> tuple[int, int, int]:
    """CIE 1931 xy (at full luminance) to display sRGB (for display only)."""
    if y <= 0:
        return (0, 0, 0)
    Y = 1.0
    X = x * Y / y
    Z = (1 - x - y) * Y / y
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    b = 0.0557 * X - 0.2040 * Y + 1.0570 * Z
    r, g, b = (max(0.0, v) for v in (r, g, b))
    m = max(r, g, b, 1e-6)

    def gamma(v: float) -> int:
        v = v / m
        v = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        return int(max(0, min(255, round(v * 255))))

    return gamma(r), gamma(g), gamma(b)
