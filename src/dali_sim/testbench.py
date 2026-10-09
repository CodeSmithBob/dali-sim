"""Pre-compliance checks: protocol behaviour tests that run against any
transport, virtual or real.

These are *not* the DALI Alliance certification tests (those test sequences
are only available to Alliance members and are run by approved test houses).
They check the behaviour described in public DALI literature and catch the
common mistakes early: answers, send-twice handling, reset values, limits,
groups, scenes, fading, memory bank 0, addressing conflicts.

Every suite restores what it changes (a RESET at the end of each unit).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

from .controller import Controller, Transport, _maybe
from .protocol.address import DeviceAddress
from .protocol.frames import Frame

ProgressFn = Callable[[dict], Awaitable[None] | None]


@dataclass
class Result:
    suite: str
    test: str
    target: str
    passed: bool
    detail: str = ""


class Bench:
    def __init__(self, transport: Transport, progress: ProgressFn | None = None) -> None:
        self.c = Controller(transport, source="testbench")
        self.results: list[Result] = []
        self.progress = progress

    async def sleep(self, seconds: float) -> None:
        """Wait in bus time: advance a manual clock, or sleep in real time."""
        from .bus import Bus
        from .clock import ManualClock

        t = self.c.t
        if isinstance(t, Bus) and isinstance(t.clock, ManualClock):
            t.advance(seconds)
        else:
            await asyncio.sleep(seconds)

    async def check(self, suite: str, test: str, target: str, passed: bool, detail: str = "") -> bool:
        r = Result(suite, test, target, bool(passed), detail)
        self.results.append(r)
        if self.progress:
            await _maybe(self.progress({"phase": "test", **asdict(r)}))
        return r.passed

    # -- control gear -------------------------------------------------------------
    async def gear_unit(self, a: int, fade_check: bool = True) -> None:  # noqa: C901
        c, s, tgt = self.c, "gear", f"A{a}"
        q = c.query
        if not await self.check(s, "QUERY CONTROL GEAR PRESENT answers YES", tgt, await c.query_yes(a, 0x91)):
            return
        await c.reset(a)
        await self.sleep(0.35)  # gear may need up to 300 ms after RESET
        await self.check(s, "RESET -> QUERY RESET STATE is YES", tgt, await c.query_yes(a, 0x95))
        vals = {"power on level": (0xA3, 254), "system failure level": (0xA4, 254), "max level": (0xA1, 254),
                "fade time/rate": (0xA5, 0x07), "groups 0-7": (0xC0, 0), "groups 8-15": (0xC1, 0),
                "scene 0": (0xB0, 0xFF), "scene 15": (0xBF, 0xFF)}
        for name, (op, want) in vals.items():
            got = await q(a, op)
            await self.check(s, f"reset value of {name}", tgt, got == want, f"got {got}, want {want}")
        phm = await q(a, 0x9A)
        got = await q(a, 0xA2)
        await self.check(s, "reset min level = physical min level", tgt, got == phm and phm is not None,
                         f"min {got}, physical min {phm}")

        # DTR round trips
        for reg, setop, qop in (("DTR0", 0xA3, 0x98), ("DTR1", 0xC3, 0x9C), ("DTR2", 0xC5, 0x9D)):
            await c.special(setop, 0x5A)
            got = await q(a, qop)
            await self.check(s, f"{reg} write/read back", tgt, got == 0x5A, f"got {got}")

        # send twice
        await c.dtr0(200)
        await c.gear_cmd(a, 0x2A)  # SET MAX LEVEL once
        got = await q(a, 0xA1)
        await self.check(s, "config command sent once is ignored", tgt, got == 254, f"max level {got}")
        await c.dtr0(200)
        f = Frame.forward16((a << 1) | 1, 0x2A)
        await c._send([f])
        await self.sleep(0.15)
        await c._send([f])
        got = await q(a, 0xA1)
        await self.check(s, "send-twice with >100 ms gap is ignored", tgt, got == 254, f"max level {got}")
        await c.set_max_level(a, 200)
        got = await q(a, 0xA1)
        await self.check(s, "config command sent twice is executed", tgt, got == 200, f"max level {got}")

        # arc power and limits
        await c.dapc(a, 254)
        got = await q(a, 0xA0)
        await self.check(s, "DAPC above max level is limited to max", tgt, got == 200, f"actual {got}")
        await self.check(s, "limit error reported", tgt, await c.query_yes(a, 0x94))
        await c.off(a)
        got = await q(a, 0xA0)
        await self.check(s, "OFF -> actual level 0", tgt, got == 0, f"actual {got}")
        await self.check(s, "lamp power on is NO when off", tgt, not await c.query_yes(a, 0x93))
        await c.recall_min(a)
        got = await q(a, 0xA0)
        await self.check(s, "RECALL MIN LEVEL", tgt, got == phm, f"actual {got}")
        await c.gear_cmd(a, 0x08)  # ON AND STEP UP / STEP UP
        got2 = await q(a, 0xA0)
        await self.check(s, "ON AND STEP UP raises the level by one step", tgt,
                         got is not None and got2 == got + 1, f"{got} -> {got2}")

        # groups
        await c.add_to_group(a, 5)
        g = await q(a, 0xC0)
        await self.check(s, "ADD TO GROUP 5", tgt, g is not None and bool(g & 0x20), f"groups {g}")
        await c.dapc("G5", 150)
        got = await q(a, 0xA0)
        await self.check(s, "group-addressed DAPC", tgt, got == 150, f"actual {got}")
        await c.remove_from_group(a, 5)
        g = await q(a, 0xC0)
        await self.check(s, "REMOVE FROM GROUP 5", tgt, g == 0, f"groups {g}")

        # scenes
        await c.set_scene(a, 3, 120)
        got = await q(a, 0xB3)
        await self.check(s, "SET SCENE 3 stored", tgt, got == 120, f"scene {got}")
        await c.go_to_scene(a, 3)
        got = await q(a, 0xA0)
        await self.check(s, "GO TO SCENE 3 (no fade)", tgt, got == 120, f"actual {got}")

        # fading
        if fade_check:
            await c.set_fade_time(a, 3)  # 1.41 s
            await c.dapc(a, 200)
            st = await q(a, 0x90)
            await self.check(s, "status shows fade running", tgt, st is not None and bool(st & 0x10), f"status {st}")
            waited = 0.0
            while waited < 5:
                st = await q(a, 0x90)
                if st is not None and not st & 0x10:
                    break
                await self.sleep(0.1)
                waited += 0.1
            got = await q(a, 0xA0)
            await self.check(s, "fade ends at target level", tgt, got == 200, f"actual {got} after ~{waited:.1f}s")

        # memory bank 0
        mem = await c.read_memory(a, 0, 0, 0x1B)
        last = mem[0]
        await self.check(s, "memory bank 0 readable", tgt, last is not None and last >= 0x1A, f"last location {last}")
        if mem[0x16] is not None:
            await self.check(s, "part 102 version (bank 0, 0x16)", tgt, mem[0x16] >= 8,
                             f"0x{mem[0x16]:02X} ({mem[0x16] >> 2}.{mem[0x16] & 3})")
        dt = await q(a, 0x99)
        await self.check(s, "QUERY DEVICE TYPE answers", tgt, dt is not None, f"device type {dt}")

        await c.reset(a)
        await self.sleep(0.35)

    # -- control devices ----------------------------------------------------------------
    async def device_unit(self, a: int) -> None:
        c, s, tgt = self.c, "devices", f"D{a}"
        addr = DeviceAddress.short(a)
        st = await c.device_query(addr, 0xFF, 0x30)
        if not await self.check(s, "QUERY DEVICE STATUS answers", tgt, st is not None, f"status {st}"):
            return
        await c.device_cmd(addr, 0xFF, 0x10, twice=True)  # RESET
        await self.sleep(0.35)
        await self.check(s, "RESET -> QUERY RESET STATE is YES", tgt,
                         (await c.device_cmd(addr, 0xFF, 0x48, answer=True)).yes)
        for reg, sub, qop in (("DTR0", 0x30, 0x36), ("DTR1", 0x31, 0x37), ("DTR2", 0x32, 0x38)):
            await c.device_special(sub, 0xA5)
            got = await c.device_query(addr, 0xFF, qop)
            await self.check(s, f"{reg} write/read back", tgt, got == 0xA5, f"got {got}")
        n = await c.device_query(addr, 0xFF, 0x35)
        await self.check(s, "QUERY NUMBER OF INSTANCES", tgt, n is not None and n <= 32, f"{n}")
        for i in range(n or 0):
            it = await c.device_query(addr, i, 0x80)
            await self.check(s, f"instance {i}: QUERY INSTANCE TYPE", tgt, it is not None, f"type {it}")
            await c.device_special(0x30, 2)
            await c.device_cmd(addr, i, 0x67, twice=True)  # SET EVENT SCHEME 2
            got = await c.device_query(addr, i, 0x8B)
            await self.check(s, f"instance {i}: SET EVENT SCHEME", tgt, got == 2, f"scheme {got}")
            await c.device_cmd(addr, i, 0x63, twice=True)  # DISABLE INSTANCE
            en = (await c.device_cmd(addr, i, 0x86, answer=True)).yes
            await self.check(s, f"instance {i}: DISABLE INSTANCE", tgt, not en)
            await c.device_cmd(addr, i, 0x62, twice=True)
        await c.device_cmd(addr, 0xFF, 0x1D, twice=True)  # START QUIESCENT MODE
        await self.check(s, "START QUIESCENT MODE", tgt, (await c.device_cmd(addr, 0xFF, 0x40, answer=True)).yes)
        await c.device_cmd(addr, 0xFF, 0x1E, twice=True)
        await self.check(s, "STOP QUIESCENT MODE", tgt, not (await c.device_cmd(addr, 0xFF, 0x40, answer=True)).yes)
        await c.device_cmd(addr, 0xFF, 0x10, twice=True)
        await self.sleep(0.35)

    # -- whole bus -------------------------------------------------------------------------
    async def bus_checks(self) -> dict[str, Any]:
        c, s = self.c, "bus"
        gear, devices, conflicts = [], [], []
        t0 = time.monotonic()
        for a in range(64):
            if (await c.gear_cmd(a, 0x91, answer=True)).yes:
                gear.append(a)
                # two units sharing a short address answer different random addresses
                rs = [await c.gear_cmd(a, op, answer=True) for op in (0xC2, 0xC3, 0xC4)]
                if any(r.collision for r in rs):
                    conflicts.append(f"A{a}")
        took = time.monotonic() - t0
        for a in range(64):
            if (await c.device_cmd(DeviceAddress.short(a), 0xFF, 0x30, answer=True)).yes:
                devices.append(a)
        unaddressed = (await c.gear_cmd("BC", 0x96, answer=True)).yes
        await self.check(s, "no short address used twice (gear)", "bus", not conflicts, ", ".join(conflicts))
        await self.check(s, "all gear have a short address", "bus", not unaddressed)
        await self.check(s, "gear count within 64", "bus", len(gear) <= 64, f"{len(gear)} gear, {len(devices)} devices")
        return {"gear": gear, "devices": devices, "scan_seconds": round(took, 2)}


SUITES = ("gear", "devices", "bus", "all")


async def run_suite(transport: Transport, suite: str = "gear", addresses: list[int] | None = None,
                    progress: ProgressFn | None = None, fade_check: bool = True) -> dict[str, Any]:
    if suite not in SUITES:
        raise ValueError(f"suite must be one of {SUITES}")
    bench = Bench(transport, progress)
    info = await bench.bus_checks() if suite in ("bus", "all") or addresses is None else {}
    if suite in ("gear", "all"):
        for a in addresses if addresses is not None else info.get("gear", []):
            await bench.gear_unit(int(a), fade_check=fade_check)
    if suite in ("devices", "all"):
        for a in addresses if addresses is not None else info.get("devices", []):
            await bench.device_unit(int(a))
    passed = sum(r.passed for r in bench.results)
    return {"suite": suite, "passed": passed, "failed": len(bench.results) - passed,
            "results": [asdict(r) for r in bench.results], "bus": info}
