"""A DALI application controller API: send commands, query devices, and
commission (address) a bus.

The controller works on any *transport* with an async ``send(frames, ...)``
method returning a :class:`~dali_sim.bus.Reply`: the simulated
:class:`~dali_sim.bus.Bus`, a hardware adapter, or a bridge.  That way the
same commissioning code runs against virtual and real devices.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .bus import Reply
from .common import MASK
from .protocol.address import DeviceAddress, GearAddress, parse_gear_target
from .protocol.frames import Frame


class Transport(Protocol):
    async def send(self, frames: Frame | list[Frame], **kw: Any) -> Reply: ...


ProgressFn = Callable[[dict], Awaitable[None] | None]


@dataclass
class FoundUnit:
    short_address: int
    random_address: int
    kind: str  # "gear" | "device"


@dataclass
class ScanResult:
    gear: dict[int, dict] = field(default_factory=dict)
    devices: dict[int, dict] = field(default_factory=dict)


def _addr(target: int | str) -> int:
    if isinstance(target, str):
        return parse_gear_target(target)
    return GearAddress.short(target)


class Controller:
    """High-level commands.  ``target`` is a short address (int) or a string
    like ``"A5"``, ``"G2"``, ``"BC"``."""

    def __init__(self, transport: Transport, *, source: str = "controller", priority: int = 3) -> None:
        self.t = transport
        self.source = source
        self.priority = priority

    async def _send(self, frames: Frame | list[Frame], **kw: Any) -> Reply:
        kw.setdefault("source", self.source)
        kw.setdefault("priority", self.priority)
        return await self.t.send(frames, **kw)

    # -- raw ------------------------------------------------------------------
    async def gear_cmd(self, target: int | str, opcode: int, *, twice: bool = False, dt: int | None = None,
                       answer: bool | None = None) -> Reply:
        f = Frame.forward16(_addr(target), opcode)
        frames: list[Frame] = []
        if dt is not None:
            frames.append(Frame.forward16(0xC1, dt))
        frames.append(f)
        if twice:
            frames.append(f)
        return await self._send(frames, expect_answer=answer)

    async def special(self, address_byte: int, data: int = 0, *, twice: bool = False, answer: bool | None = None) -> Reply:
        f = Frame.forward16(address_byte, data)
        return await self._send([f, f] if twice else [f], expect_answer=answer)

    async def device_cmd(self, address: int, instance: int, opcode: int, *, twice: bool = False,
                         answer: bool | None = None) -> Reply:
        f = Frame.forward24(address, instance, opcode)
        return await self._send([f, f] if twice else [f], expect_answer=answer)

    async def device_special(self, opcode: int, data: int = 0, *, twice: bool = False, answer: bool | None = None) -> Reply:
        f = Frame.forward24(0xC1, opcode, data)
        return await self._send([f, f] if twice else [f], expect_answer=answer)

    # -- arc power --------------------------------------------------------------
    async def dapc(self, target: int | str, level: int) -> Reply:
        return await self._send(Frame.forward16(_addr(target) & 0xFE, level & 0xFF))

    async def off(self, target: int | str) -> Reply:
        return await self.gear_cmd(target, 0x00)

    async def recall_max(self, target: int | str) -> Reply:
        return await self.gear_cmd(target, 0x05)

    async def recall_min(self, target: int | str) -> Reply:
        return await self.gear_cmd(target, 0x06)

    async def up(self, target: int | str) -> Reply:
        return await self.gear_cmd(target, 0x01)

    async def down(self, target: int | str) -> Reply:
        return await self.gear_cmd(target, 0x02)

    async def go_to_scene(self, target: int | str, scene: int) -> Reply:
        return await self.gear_cmd(target, 0x10 | (scene & 0x0F))

    async def go_to_last_active_level(self, target: int | str) -> Reply:
        return await self.gear_cmd(target, 0x0A)

    # -- configuration (DTR0 + send twice) ----------------------------------------
    async def dtr0(self, value: int) -> Reply:
        return await self.special(0xA3, value)

    async def dtr1(self, value: int) -> Reply:
        return await self.special(0xC3, value)

    async def dtr2(self, value: int) -> Reply:
        return await self.special(0xC5, value)

    async def _config(self, target: int | str, opcode: int, value: int | None = None) -> Reply:
        frames = []
        if value is not None:
            frames.append(Frame.forward16(0xA3, value & 0xFF))
        f = Frame.forward16(_addr(target), opcode)
        frames += [f, f]
        return await self._send(frames)

    async def set_max_level(self, target, v: int) -> Reply:
        return await self._config(target, 0x2A, v)

    async def set_min_level(self, target, v: int) -> Reply:
        return await self._config(target, 0x2B, v)

    async def set_system_failure_level(self, target, v: int) -> Reply:
        return await self._config(target, 0x2C, v)

    async def set_power_on_level(self, target, v: int) -> Reply:
        return await self._config(target, 0x2D, v)

    async def set_fade_time(self, target, v: int) -> Reply:
        return await self._config(target, 0x2E, v)

    async def set_fade_rate(self, target, v: int) -> Reply:
        return await self._config(target, 0x2F, v)

    async def set_extended_fade_time(self, target, base: int, multiplier: int) -> Reply:
        return await self._config(target, 0x30, (multiplier << 4) | base)

    async def set_scene(self, target, scene: int, level: int) -> Reply:
        return await self._config(target, 0x40 | (scene & 0x0F), level)

    async def remove_from_scene(self, target, scene: int) -> Reply:
        return await self._config(target, 0x50 | (scene & 0x0F))

    async def add_to_group(self, target, group: int) -> Reply:
        return await self._config(target, 0x60 | (group & 0x0F))

    async def remove_from_group(self, target, group: int) -> Reply:
        return await self._config(target, 0x70 | (group & 0x0F))

    async def set_short_address(self, target, new: int | None) -> Reply:
        return await self._config(target, 0x80, MASK if new is None else (new << 1) | 1)

    async def reset(self, target) -> Reply:
        return await self._config(target, 0x20)

    async def identify(self, target) -> Reply:
        return await self._config(target, 0x25)

    # -- colour (DT8) ---------------------------------------------------------------
    async def set_colour_temperature(self, target, kelvin: float, activate: bool = True) -> Reply:
        mirek = int(round(1e6 / kelvin))
        await self.dtr0(mirek & 0xFF)
        await self.dtr1(mirek >> 8)
        r = await self.gear_cmd(target, 0xE7, dt=8)
        if activate:
            r = await self.gear_cmd(target, 0xE2, dt=8)
        return r

    async def set_rgb(self, target, r: int, g: int, b: int, activate: bool = True) -> Reply:
        await self.dtr0(r)
        await self.dtr1(g)
        await self.dtr2(b)
        rep = await self.gear_cmd(target, 0xEB, dt=8)
        if activate:
            rep = await self.gear_cmd(target, 0xE2, dt=8)
        return rep

    async def set_xy(self, target, x: float, y: float, activate: bool = True) -> Reply:
        for value, op in ((int(x * 65536) & 0xFFFF, 0xE0), (int(y * 65536) & 0xFFFF, 0xE1)):
            await self.dtr0(value & 0xFF)
            await self.dtr1(value >> 8)
            await self.gear_cmd(target, op, dt=8)
        if activate:
            return await self.gear_cmd(target, 0xE2, dt=8)
        return Reply("none")

    # -- queries ---------------------------------------------------------------------
    async def query(self, target: int | str, opcode: int, dt: int | None = None) -> int | None:
        r = await self.gear_cmd(target, opcode, dt=dt, answer=True)
        return r.value if r.answered else None

    async def query_yes(self, target: int | str, opcode: int) -> bool:
        r = await self.gear_cmd(target, opcode, answer=True)
        return r.yes

    async def query_actual_level(self, target) -> int | None:
        return await self.query(target, 0xA0)

    async def query_status(self, target) -> int | None:
        return await self.query(target, 0x90)

    async def read_memory(self, target, bank: int, start: int, length: int) -> list[int | None]:
        await self.dtr1(bank)
        await self.dtr0(start)
        out = []
        for _ in range(length):
            out.append(await self.query(target, 0xC5))
        return out

    async def device_query(self, address: int, instance: int, opcode: int) -> int | None:
        r = await self.device_cmd(address, instance, opcode, answer=True)
        return r.value if r.answered else None

    # -- scanning -------------------------------------------------------------------------
    async def scan(self, gear: bool = True, devices: bool = True, details: bool = True,
                   progress: ProgressFn | None = None) -> ScanResult:
        res = ScanResult()
        if gear:
            for a in range(64):
                r = await self.gear_cmd(a, 0x91, answer=True)
                if progress:
                    await _maybe(progress({"phase": "scan-gear", "address": a, "found": r.yes}))
                if not r.yes:
                    continue
                info: dict[str, Any] = {"collision": r.collision}
                if details:
                    info["device_type"] = await self.query(a, 0x99)
                    info["actual_level"] = await self.query(a, 0xA0)
                    info["status"] = await self.query(a, 0x90)
                    g0 = await self.query(a, 0xC0) or 0
                    g1 = await self.query(a, 0xC1) or 0
                    info["groups"] = [g for g in range(16) if ((g1 << 8) | g0) & (1 << g)]
                    bank0 = await self.read_memory(a, 0, 0x03, 16)
                    if all(v is not None for v in bank0):
                        info["gtin"] = int.from_bytes(bytes(bank0[0:6]), "big")
                        info["firmware"] = f"{bank0[6]}.{bank0[7]}"
                        info["serial"] = bytes(bank0[8:16]).hex()
                res.gear[a] = info
        if devices:
            for a in range(64):
                r = await self.device_cmd(DeviceAddress.short(a), 0xFF, 0x30, answer=True)
                if progress:
                    await _maybe(progress({"phase": "scan-devices", "address": a, "found": r.yes}))
                if not r.yes:
                    continue
                info = {"status": r.value}
                if details:
                    n = await self.device_query(DeviceAddress.short(a), 0xFF, 0x35) or 0
                    info["instances"] = []
                    for i in range(n):
                        it = await self.device_query(DeviceAddress.short(a), i, 0x80)
                        info["instances"].append({"number": i, "type": it})
                res.devices[a] = info
        return res

    # -- commissioning ------------------------------------------------------------------------
    async def commission(self, *, devices: bool = False, only_new: bool = True, start: int = 0,
                         progress: ProgressFn | None = None, delay_after_randomise: float = 0.1) -> list[FoundUnit]:
        """Give short addresses to control gear (or control devices) with the
        random-address binary search.  ``only_new`` keeps existing short
        addresses and only addresses units without one."""
        if devices:
            ops = _DeviceOps(self)
        else:
            ops = _GearOps(self)
        used: set[int] = set()
        if only_new:
            for a in range(64):
                if await ops.present(a):
                    used.add(a)
        await ops.initialise(only_new)
        await ops.randomise()
        await asyncio.sleep(delay_after_randomise)
        found: list[FoundUnit] = []
        search = [None, None, None]
        next_addr = start

        async def set_search(value: int) -> None:
            for i, shift in enumerate((16, 8, 0)):
                b = (value >> shift) & 0xFF
                if search[i] != b:
                    await ops.search_byte(i, b)
                    search[i] = b

        while True:
            await set_search(0xFFFFFF)
            if not await ops.compare():
                break
            low, high = 0, 0xFFFFFF
            while low < high:
                mid = (low + high) // 2
                await set_search(mid)
                if await ops.compare():
                    high = mid
                else:
                    low = mid + 1
                if progress:
                    await _maybe(progress({"phase": "search", "low": low, "high": high, "found": len(found)}))
            await set_search(low)
            while next_addr in used and next_addr < 64:
                next_addr += 1
            if next_addr >= 64:
                if progress:
                    await _maybe(progress({"phase": "error", "message": "no free short address left"}))
                break
            await ops.program(next_addr)
            ok = await ops.verify(next_addr)
            unit = FoundUnit(next_addr, low, "device" if devices else "gear")
            found.append(unit)
            used.add(next_addr)
            if progress:
                await _maybe(progress({"phase": "programmed", "short_address": next_addr,
                                       "random_address": f"{low:06X}", "verified": ok, "found": len(found)}))
            await ops.withdraw()
            next_addr += 1
        await ops.terminate()
        if progress:
            await _maybe(progress({"phase": "done", "found": len(found)}))
        return found


async def _maybe(x: Any) -> None:
    if asyncio.iscoroutine(x):
        await x


class _GearOps:
    def __init__(self, c: Controller) -> None:
        self.c = c

    async def present(self, a: int) -> bool:
        return await self.c.query_yes(a, 0x91)

    async def initialise(self, only_new: bool) -> None:
        await self.c.special(0xA5, 0xFF if only_new else 0x00, twice=True)

    async def randomise(self) -> None:
        await self.c.special(0xA7, 0x00, twice=True)

    async def search_byte(self, i: int, b: int) -> None:
        await self.c.special((0xB1, 0xB3, 0xB5)[i], b)

    async def compare(self) -> bool:
        return (await self.c.special(0xA9, 0x00, answer=True)).yes

    async def program(self, a: int) -> None:
        await self.c.special(0xB7, (a << 1) | 1)

    async def verify(self, a: int) -> bool:
        return (await self.c.special(0xB9, (a << 1) | 1, answer=True)).yes

    async def withdraw(self) -> None:
        await self.c.special(0xAB, 0x00)

    async def terminate(self) -> None:
        await self.c.special(0xA1, 0x00)


class _DeviceOps(_GearOps):
    async def present(self, a: int) -> bool:
        return (await self.c.device_cmd(DeviceAddress.short(a), 0xFF, 0x30, answer=True)).yes

    async def initialise(self, only_new: bool) -> None:
        await self.c.device_special(0x01, 0x7F if only_new else 0xFF, twice=True)

    async def randomise(self) -> None:
        await self.c.device_special(0x02, 0x00, twice=True)

    async def search_byte(self, i: int, b: int) -> None:
        await self.c.device_special(0x05 + i, b)

    async def compare(self) -> bool:
        return (await self.c.device_special(0x03, 0x00, answer=True)).yes

    async def program(self, a: int) -> None:
        await self.c.device_special(0x08, a)

    async def verify(self, a: int) -> bool:
        return (await self.c.device_special(0x09, a, answer=True)).yes

    async def withdraw(self) -> None:
        await self.c.device_special(0x04, 0x00)

    async def terminate(self) -> None:
        await self.c.device_special(0x00, 0x00)
