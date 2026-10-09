"""A DALI-2 control gear (IEC 62386-102): ballast, LED driver, relay...

The model keeps the variables named in part 102 and implements every
standard and special command.  Device-type specific behaviour (LED, colour,
emergency...) plugs in through :class:`GearExtension`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common import MASK, YES, InitState, Unit
from ..memory import DALI2_VERSION, MemoryBank, make_bank0, make_bank1, version_byte
from ..protocol import timing as T
from ..protocol.address import AddrKind, GearAddress
from ..protocol.commands import GEAR_COMMANDS, GEAR_SPECIAL
from ..protocol.dimming import (
    decode_extended_fade,
    extended_fade_seconds,
    fade_rate_steps_per_second,
    fade_time_seconds,
    linear_curve_percent,
    log_curve_percent,
)
from ..protocol.frames import Frame


class LightSource:
    """Values for QUERY LIGHT SOURCE TYPE."""

    LOW_PRESSURE_FLUORESCENT = 0
    HID = 2
    LOW_VOLTAGE_HALOGEN = 3
    INCANDESCENT = 4
    LED = 6
    OLED = 7
    OTHER = 252
    UNKNOWN = 253
    NONE = 254
    MULTIPLE = MASK


@dataclass
class Fade:
    t0: float
    l0: int
    t1: float
    l1: int
    final: int  # level once the fade ends (0 for a fade to off)


class GearExtension:
    """Base class for device-type extensions (application extended commands)."""

    device_type = 0
    version = version_byte(2, 0)
    commands: dict = {}

    def __init__(self) -> None:
        self.gear: ControlGear | None = None

    def attach(self, gear: ControlGear) -> None:
        self.gear = gear

    def command(self, opcode: int, t: float, confirmed: bool) -> int | None:
        """Handle an application extended command.  ``confirmed`` is True if
        a send-twice command arrived for the second time."""
        if opcode == 0xFF:
            return self.version
        return None

    # hooks ---------------------------------------------------------------
    def on_arc_power(self, t: float, duration: float, scene: int | None = None) -> None:
        pass

    def on_store_scene(self, scene: int) -> None:
        pass

    def on_remove_scene(self, scene: int) -> None:
        pass

    def on_reset(self) -> None:
        pass

    def light_percent(self, level: int) -> float | None:
        """Override the dimming curve (e.g. DT6 linear curve)."""
        return None

    def state(self, t: float) -> dict[str, Any]:
        return {}

    def tick(self, t: float) -> None:
        pass


class ControlGear(Unit):
    kind = "gear"

    def __init__(self, name: str, *, short_address: int | None = None, physical_min_level: int = 1,
                 light_source: int = LightSource.LED, extensions: list[GearExtension] | None = None,
                 dali_version: int = 2, gtin: int = 0x0000_1234_5678, serial: int | None = None,
                 random_address: int | None = None, seed: int | None = None, power_watts: float = 20.0) -> None:
        super().__init__(name, short_address=short_address, random_address=random_address, seed=seed)
        self.dali_version = dali_version
        self.physical_min_level = physical_min_level
        self.light_source = light_source
        self.power_watts = power_watts
        # persistent variables
        self.power_on_level = 254
        self.system_failure_level = 254
        self.min_level = physical_min_level
        self.max_level = 254
        self.fade_rate = 7
        self.fade_time = 0
        self.ext_fade_base = 0
        self.ext_fade_mult = 0
        self.scenes = [MASK] * 16
        self.groups = 0
        self.operating_mode = 0
        self.last_active_level = 254
        # volatile variables
        self._fade = Fade(0.0, 0, 0.0, 0, 0)
        self.limit_error = False
        self.lamp_failure = False
        self.gear_failure = False
        self.enabled_device_type: int | None = None
        self._next_dt_index = 0
        self._sysfail_timer: int | None = None
        self._last_light_level = 254
        # device types
        self.extensions: dict[int, GearExtension] = {}
        for ext in extensions or []:
            self.add_extension(ext)
        serial = self.rng.getrandbits(40) if serial is None else serial
        self.banks: dict[int, MemoryBank] = {}
        self.banks[0] = make_bank0(
            gtin=gtin, firmware=(1, 0), serial=serial, hardware=(1, 0),
            v101=DALI2_VERSION, v102=DALI2_VERSION if dali_version == 2 else version_byte(1, 0),
            v103=MASK, n_devices=0, n_gear=1, index=0, last_bank=1,
        )
        self.banks[1] = make_bank1()
        # The gear starts powered at the power-on level.
        lvl = self.power_on_level
        self._fade = Fade(0.0, lvl, 0.0, lvl, lvl)

    # ------------------------------------------------------------------
    def add_extension(self, ext: GearExtension) -> None:
        ext.attach(self)
        self.extensions[ext.device_type] = ext

    @property
    def device_types(self) -> list[int]:
        return sorted(self.extensions)

    # -- level model -----------------------------------------------------
    def level_at(self, t: float) -> int:
        f = self._fade
        if t >= f.t1 or f.t1 <= f.t0:
            return f.final
        if t <= f.t0:
            return f.l0
        frac = (t - f.t0) / (f.t1 - f.t0)
        return int(round(f.l0 + (f.l1 - f.l0) * frac))

    def actual_level(self) -> int:
        return self.level_at(self.bus.now() if self.bus else 0.0)

    def fading_at(self, t: float) -> bool:
        return self._fade.t0 <= t < self._fade.t1

    def light_percent_at(self, t: float) -> float:
        lvl = self.level_at(t)
        if not self.mains or self.lamp_failure:
            return 0.0
        for ext in self.extensions.values():
            p = ext.light_percent(lvl)
            if p is not None:
                return p
        return log_curve_percent(lvl)

    def _set_level(self, t: float, level: int) -> None:
        self._fade = Fade(t, level, t, level, level)
        if level:
            self.last_active_level = level

    def _clamp(self, level: int) -> int:
        if level == 0:
            return 0
        if level > self.max_level:
            self.limit_error = True
            return self.max_level
        if level < self.min_level:
            self.limit_error = True
            return self.min_level
        self.limit_error = False
        return level

    def effective_fade_time(self) -> float:
        if self.fade_time:
            return fade_time_seconds(self.fade_time)
        return extended_fade_seconds(self.ext_fade_base, self.ext_fade_mult)

    def _go_to(self, t: float, level: int, duration: float | None = None, scene: int | None = None) -> None:
        """Arc power change with fade (DAPC, GO TO SCENE...)."""
        if duration is None:
            duration = self.effective_fade_time()
        cur = self.level_at(t)
        target = self._clamp(level)
        if target == 0:
            if duration <= 0 or cur == 0:
                self._fade = Fade(t, 0, t, 0, 0)
            else:
                self._fade = Fade(t, cur, t + duration, self.min_level, 0)
        else:
            start = cur if cur else self.min_level
            if duration <= 0:
                self._fade = Fade(t, target, t, target, target)
            else:
                self._fade = Fade(t, start, t + duration, target, target)
            self.last_active_level = target
        for ext in self.extensions.values():
            ext.on_arc_power(t, duration, scene)

    def _fade_by_rate(self, t: float, direction: int, seconds: float | None = T.UP_DOWN_DURATION) -> None:
        cur = self.level_at(t)
        if cur == 0:
            return  # UP/DOWN never switch the lamp on or off
        rate = fade_rate_steps_per_second(self.fade_rate)
        limit = self.max_level if direction > 0 else self.min_level
        if seconds is None:  # continuous: run to the limit
            steps = abs(limit - cur)
        else:
            steps = rate * seconds
        target = cur + direction * steps
        target = int(round(min(target, limit) if direction > 0 else max(target, limit)))
        if target == cur:
            return
        duration = abs(target - cur) / rate
        self._fade = Fade(t, cur, t + duration, target, target)
        self.last_active_level = target
        for ext in self.extensions.values():
            ext.on_arc_power(t, duration, None)

    # -- status ------------------------------------------------------------
    def status_byte(self, t: float) -> int:
        lvl = self.level_at(t)
        bits = [
            self.gear_failure,
            self.lamp_failure,
            lvl > 0 and not self.lamp_failure,
            self.limit_error,
            self.fading_at(t),
            self.reset_state(),
            self.short_address == MASK,
            self.power_cycle_seen,
        ]
        return sum(int(b) << i for i, b in enumerate(bits))

    def reset_state(self) -> bool:
        return (
            self.power_on_level == 254 and self.system_failure_level == 254
            and self.min_level == self.physical_min_level and self.max_level == 254
            and self.fade_rate == 7 and self.fade_time == 0
            and self.ext_fade_base == 0 and self.ext_fade_mult == 0
            and self.scenes == [MASK] * 16 and self.groups == 0
            and self.random_address == 0xFFFFFF and self.search_address == 0xFFFFFF
            and self.operating_mode == 0
        )

    def _reset(self, t: float) -> None:
        self.power_on_level = 254
        self.system_failure_level = 254
        self.min_level = self.physical_min_level
        self.max_level = 254
        self.fade_rate = 7
        self.fade_time = 0
        self.ext_fade_base = 0
        self.ext_fade_mult = 0
        self.scenes = [MASK] * 16
        self.groups = 0
        self.random_address = 0xFFFFFF
        self.search_address = 0xFFFFFF
        self.operating_mode = 0
        self.last_active_level = 254
        self.limit_error = False
        self.power_cycle_seen = False
        self._set_level(t, 254)
        for ext in self.extensions.values():
            ext.on_reset()

    # -- power -------------------------------------------------------------
    def set_mains(self, on: bool) -> None:
        t = self.bus.now() if self.bus else 0.0
        if on == self.mains:
            return
        if not on:
            self._last_light_level = self.level_at(t)
            self.mains = False
            self._fade = Fade(t, 0, t, 0, 0)
            self.init_state = InitState.DISABLED
            self.twice.clear()
            return
        self.mains = True
        self.power_cycle_seen = True
        self.enabled_device_type = None
        self.dtr0 = self.dtr1 = self.dtr2 = 0
        lvl = self._last_light_level if self.power_on_level == MASK else self.power_on_level
        self._set_level(t, self._clamp(lvl) if lvl else 0)

    def on_bus_power(self, powered: bool, t: float) -> None:
        if self.bus is None:
            return
        self.bus.cancel(self._sysfail_timer)
        self._sysfail_timer = None
        if not powered:
            self._sysfail_timer = self.bus.call_at(t + T.SYSTEM_FAILURE_TIMEOUT, self._system_failure)

    def _system_failure(self, t: float) -> None:
        self._sysfail_timer = None
        if self.bus and not self.bus.powered and self.mains and self.system_failure_level != MASK:
            self._set_level(t, self._clamp(self.system_failure_level) if self.system_failure_level else 0)

    # -- addressing ----------------------------------------------------------
    def addressed(self, addr: GearAddress) -> bool:
        if addr.kind == AddrKind.SHORT:
            return addr.value == self.short_address
        if addr.kind == AddrKind.GROUP:
            return bool(self.groups & (1 << addr.value))
        if addr.kind == AddrKind.BROADCAST:
            return True
        if addr.kind == AddrKind.BROADCAST_UNADDRESSED:
            return self.short_address == MASK
        return False

    # -- frame entry point -----------------------------------------------------
    def on_frame(self, frame: Frame, t: float, source: str) -> int | None:
        if frame.bits != 16 or not self.online:
            if frame.is_forward:
                self.twice.other_frame(frame)
            return None
        a, b = frame.byte(0), frame.byte(1)
        addr = GearAddress.decode(a)
        enabled_dt = self.enabled_device_type
        if not (addr.kind == AddrKind.SPECIAL and a == 0xC1):
            self.twice.other_frame(frame)
            self.enabled_device_type = None
        if addr.kind == AddrKind.SPECIAL:
            return self._special(a, b, frame, t)
        if addr.kind == AddrKind.RESERVED or not self.addressed(addr):
            return None
        if not addr.is_command:
            self._disable_write()
            self._dapc(b, t)
            return None
        if b >= 0xE0:
            return self._extended(b, frame, t, enabled_dt)
        cmd = GEAR_COMMANDS.get(b)
        if cmd is None:
            return None
        confirmed = False
        if cmd.twice:
            confirmed = self.twice.confirm(frame, t)
            if not confirmed:
                return None
        if b not in (0x81,) and not 0x98 <= b <= 0x9D and b != 0xC5:
            self._disable_write()
        return self._command(b, t)

    def _disable_write(self) -> None:
        self.write_enabled = False

    def _extended(self, b: int, frame: Frame, t: float, enabled_dt: int | None) -> int | None:
        if enabled_dt is None:
            return None
        ext = self.extensions.get(enabled_dt)
        if ext is None:
            return None
        cmd = ext.commands.get(b)
        confirmed = False
        if cmd is not None and cmd.twice:
            confirmed = self.twice.confirm(frame, t)
            if not confirmed:
                # Keep the device type enabled for the repeat.
                self.enabled_device_type = enabled_dt
                return None
        return ext.command(b, t, confirmed)

    # -- direct arc power ------------------------------------------------------
    def _dapc(self, level: int, t: float) -> None:
        if level == MASK:
            cur = self.level_at(t)
            self._fade = Fade(t, cur, t, cur, cur)  # stop fading
            return
        self._go_to(t, level)

    # -- standard commands -------------------------------------------------------
    def _command(self, op: int, t: float) -> int | None:  # noqa: C901 - a command table
        lvl = self.level_at(t)
        if op == 0x00:  # OFF
            self._set_level(t, 0)
            for ext in self.extensions.values():
                ext.on_arc_power(t, 0, None)
        elif op == 0x01:  # UP
            self._fade_by_rate(t, +1)
        elif op == 0x02:  # DOWN
            self._fade_by_rate(t, -1)
        elif op == 0x03:  # STEP UP
            if lvl and lvl < self.max_level:
                self._set_level(t, lvl + 1)
        elif op == 0x04:  # STEP DOWN
            if lvl and lvl > self.min_level:
                self._set_level(t, lvl - 1)
        elif op == 0x05:  # RECALL MAX LEVEL
            self._set_level(t, self.max_level)
            for ext in self.extensions.values():
                ext.on_arc_power(t, 0, None)
        elif op == 0x06:  # RECALL MIN LEVEL
            self._set_level(t, self.min_level)
            for ext in self.extensions.values():
                ext.on_arc_power(t, 0, None)
        elif op == 0x07:  # STEP DOWN AND OFF
            if lvl:
                self._set_level(t, 0 if lvl <= self.min_level else lvl - 1)
        elif op == 0x08:  # ON AND STEP UP
            if lvl == 0:
                self._set_level(t, self.min_level)
            elif lvl < self.max_level:
                self._set_level(t, lvl + 1)
        elif op == 0x09:  # ENABLE DAPC SEQUENCE
            pass
        elif op == 0x0A:  # GO TO LAST ACTIVE LEVEL
            self._go_to(t, self.last_active_level)
        elif op == 0x0B:  # CONTINUOUS UP
            self._fade_by_rate(t, +1, None)
        elif op == 0x0C:  # CONTINUOUS DOWN
            self._fade_by_rate(t, -1, None)
        elif 0x10 <= op <= 0x1F:  # GO TO SCENE
            n = op & 0x0F
            if self.scenes[n] != MASK:
                self._go_to(t, self.scenes[n], scene=n)
        elif op == 0x20:
            self._reset(t)
        elif op == 0x21:
            self.dtr0 = lvl
        elif op == 0x22:  # SAVE PERSISTENT VARIABLES
            pass
        elif op == 0x23:
            if self.dtr0 == 0 or 0x80 <= self.dtr0 <= 0xFF and self.dtr0 in self.supported_modes():
                self.operating_mode = self.dtr0
        elif op == 0x24:  # RESET MEMORY BANK
            for n, bank in self.banks.items():
                if n and (self.dtr0 == 0 or self.dtr0 == n):
                    bank.reset()
        elif op == 0x25:
            self._identify(t)
        elif op == 0x2A:  # SET MAX LEVEL
            v = 254 if self.dtr0 == MASK else max(self.dtr0, self.min_level)
            self.max_level = v
            if lvl > v:
                self._set_level(t, v)
        elif op == 0x2B:  # SET MIN LEVEL
            v = self.dtr0
            if v < self.physical_min_level:
                v = self.physical_min_level
            if v > self.max_level:
                v = self.max_level
            self.min_level = v
            if 0 < lvl < v:
                self._set_level(t, v)
        elif op == 0x2C:
            self.system_failure_level = self.dtr0
        elif op == 0x2D:
            self.power_on_level = self.dtr0
        elif op == 0x2E:
            self.fade_time = min(self.dtr0, 15)
        elif op == 0x2F:
            self.fade_rate = min(max(self.dtr0, 1), 15)
        elif op == 0x30:  # SET EXTENDED FADE TIME
            ext = decode_extended_fade(self.dtr0)
            if ext is None:
                self.ext_fade_base, self.ext_fade_mult = 0, 0
            else:
                self.ext_fade_base, self.ext_fade_mult = ext
        elif 0x40 <= op <= 0x4F:  # SET SCENE
            n = op & 0x0F
            self.scenes[n] = self.dtr0
            for ext in self.extensions.values():
                if self.dtr0 == MASK:
                    ext.on_remove_scene(n)
                else:
                    ext.on_store_scene(n)
        elif 0x50 <= op <= 0x5F:  # REMOVE FROM SCENE
            self.scenes[op & 0x0F] = MASK
            for ext in self.extensions.values():
                ext.on_remove_scene(op & 0x0F)
        elif 0x60 <= op <= 0x6F:
            self.groups |= 1 << (op & 0x0F)
        elif 0x70 <= op <= 0x7F:
            self.groups &= ~(1 << (op & 0x0F))
        elif op == 0x80:  # SET SHORT ADDRESS
            if self.dtr0 == MASK:
                self.short_address = MASK
            elif self.dtr0 & 1 and self.dtr0 < 0x80:
                self.short_address = self.dtr0 >> 1
        elif op == 0x81:
            self.write_enabled = True
        # ---- queries ----
        elif op == 0x90:
            return self.status_byte(t)
        elif op == 0x91:
            return YES
        elif op == 0x92:
            return YES if self.lamp_failure else None
        elif op == 0x93:
            return YES if lvl > 0 and not self.lamp_failure else None
        elif op == 0x94:
            return YES if self.limit_error else None
        elif op == 0x95:
            return YES if self.reset_state() else None
        elif op == 0x96:
            return YES if self.short_address == MASK else None
        elif op == 0x97:
            return self.banks[0].data[0x16]
        elif op == 0x98:
            return self.dtr0
        elif op == 0x99:
            types = self.device_types
            self._next_dt_index = 0
            if not types:
                return 254 if self.dali_version == 2 else 0
            if len(types) == 1:
                return types[0]
            return MASK
        elif op == 0x9A:
            return self.physical_min_level
        elif op == 0x9B:
            return YES if self.power_cycle_seen else None
        elif op == 0x9C:
            return self.dtr1
        elif op == 0x9D:
            return self.dtr2
        elif op == 0x9E:
            return self.operating_mode
        elif op == 0x9F:
            return self.light_source
        elif op == 0xA0:
            return lvl
        elif op == 0xA1:
            return self.max_level
        elif op == 0xA2:
            return self.min_level
        elif op == 0xA3:
            return self.power_on_level
        elif op == 0xA4:
            return self.system_failure_level
        elif op == 0xA5:
            return (self.fade_time << 4) | self.fade_rate
        elif op == 0xA6:
            return YES if self.operating_mode >= 0x80 else None
        elif op == 0xA7:  # QUERY NEXT DEVICE TYPE
            types = self.device_types
            if len(types) < 2:
                return None
            if self._next_dt_index < len(types):
                v = types[self._next_dt_index]
                self._next_dt_index += 1
                return v
            return 254
        elif op == 0xA8:
            return (self.ext_fade_mult << 4) | self.ext_fade_base
        elif op == 0xAA:
            return YES if self.gear_failure else None
        elif 0xB0 <= op <= 0xBF:
            return self.scenes[op & 0x0F]
        elif op == 0xC0:
            return self.groups & 0xFF
        elif op == 0xC1:
            return (self.groups >> 8) & 0xFF
        elif op == 0xC2:
            return (self.random_address >> 16) & 0xFF
        elif op == 0xC3:
            return (self.random_address >> 8) & 0xFF
        elif op == 0xC4:
            return self.random_address & 0xFF
        elif op == 0xC5:  # READ MEMORY LOCATION
            bank = self.banks.get(self.dtr1)
            if bank is None:
                return None
            v = bank.read(self.dtr0)
            if v is None:
                return None
            if self.dtr0 < 0xFF:
                self.dtr0 += 1
            return v
        return None

    def supported_modes(self) -> set[int]:
        return {0}

    # -- special commands -------------------------------------------------------
    def _special(self, a: int, b: int, frame: Frame, t: float) -> int | None:  # noqa: C901
        cmd = GEAR_SPECIAL.get(a)
        if cmd is None:
            return None
        if cmd.twice and not self.twice.confirm(frame, t):
            return None
        init_active = self.init_state != InitState.DISABLED
        if a == 0xA1:  # TERMINATE
            self._terminate()
            self.identify_until = -1.0
        elif a == 0xA3:
            self.dtr0 = b
        elif a == 0xC3:
            self.dtr1 = b
        elif a == 0xC5:
            self.dtr2 = b
        elif a == 0xA5:  # INITIALISE
            if (b == 0x00 or (b == 0xFF and self.short_address == MASK)
                    or (b & 1 and b < 0x80 and (b >> 1) == self.short_address)):
                self._start_initialise(t)
        elif a == 0xA7:  # RANDOMISE
            if init_active:
                self._randomise()
        elif a == 0xA9:  # COMPARE
            if self.init_state == InitState.ENABLED and self.random_address <= self.search_address:
                return YES
        elif a == 0xAB:  # WITHDRAW
            if self.init_state == InitState.ENABLED and self.random_address == self.search_address:
                self.init_state = InitState.WITHDRAWN
        elif a == 0xAD:  # PING
            pass
        elif a in (0xB1, 0xB3, 0xB5):
            if init_active:
                self._set_search_byte({0xB1: 0, 0xB3: 1, 0xB5: 2}[a], b)
        elif a == 0xB7:  # PROGRAM SHORT ADDRESS
            if init_active and self.random_address == self.search_address:
                if b == MASK:
                    self.short_address = MASK
                elif b & 1 and b < 0x80:
                    self.short_address = b >> 1
        elif a == 0xB9:  # VERIFY SHORT ADDRESS
            if init_active and b & 1 and b < 0x80 and self.short_address == b >> 1:
                return YES
        elif a == 0xBB:  # QUERY SHORT ADDRESS
            if init_active and self.random_address == self.search_address:
                return MASK if self.short_address == MASK else (self.short_address << 1) | 1
        elif a == 0xBD:  # PHYSICAL SELECTION (DALI-1 only)
            pass
        elif a == 0xC1:  # ENABLE DEVICE TYPE
            self.enabled_device_type = b
        elif a in (0xC7, 0xC9):  # WRITE MEMORY LOCATION (- NO REPLY)
            return self._write_memory(b, reply=(a == 0xC7))
        return None

    def _write_memory(self, value: int, reply: bool) -> int | None:
        if not self.write_enabled:
            return None
        bank = self.banks.get(self.dtr1)
        if bank is None:
            return None
        loc = self.dtr0
        ok = bank.write(loc, value)
        if loc < 0xFF:
            self.dtr0 = loc + 1
        if ok and reply:
            return value & 0xFF
        return None

    # -- visualisation ---------------------------------------------------------
    def snapshot(self, t: float) -> dict[str, Any]:
        lvl = self.level_at(t)
        d = {
            "name": self.name,
            "kind": "gear",
            "short_address": None if self.short_address == MASK else self.short_address,
            "random_address": f"{self.random_address:06X}",
            "level": lvl,
            "percent": round(self.light_percent_at(t), 2),
            "fading": self.fading_at(t),
            "groups": [g for g in range(16) if self.groups & (1 << g)],
            "scenes": self.scenes,
            "min_level": self.min_level,
            "max_level": self.max_level,
            "power_on_level": self.power_on_level,
            "system_failure_level": self.system_failure_level,
            "fade_time": self.fade_time,
            "fade_rate": self.fade_rate,
            "status": self.status_byte(t),
            "device_types": self.device_types,
            "lamp_failure": self.lamp_failure,
            "gear_failure": self.gear_failure,
            "mains": self.mains,
            "identifying": self.identify_until > t,
            "init_state": self.init_state.value,
            "power_w": round(self.power_watts * self.light_percent_at(t) / 100, 2),
            "meta": self.meta,
        }
        for ext in self.extensions.values():
            d.update(ext.state(t))
        return d


def linear_percent(level: int) -> float:
    return linear_curve_percent(level)
