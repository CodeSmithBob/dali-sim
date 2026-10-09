"""Web server: the visualisation UI, a REST API and a WebSocket feed."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .. import __version__
from ..bus import BusRecord, Reply
from ..controller import Controller
from ..protocol import commands as C
from ..protocol.address import DeviceAddress, InstanceByte, parse_gear_target
from ..protocol.frames import Frame
from ..sim import Simulator
from .tcp import parse_frame

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


def command_catalog() -> dict[str, Any]:
    def rows(table: dict[int, C.Cmd]) -> list[dict[str, Any]]:
        seen = []
        for op, cmd in sorted(table.items()):
            seen.append({"opcode": op, "name": cmd.name_for(op), "answer": cmd.answer, "twice": cmd.twice,
                         "uses": list(cmd.uses), "dali2": cmd.dali2, "legacy": cmd.legacy})
        return seen

    return {
        "gear": rows(C.GEAR_COMMANDS),
        "gear_special": rows(C.GEAR_SPECIAL),
        "dt": {str(k): rows(v) for k, v in C.DEVICE_TYPE_COMMANDS.items()},
        "device": rows(C.DEVICE_COMMANDS),
        "instance": rows(C.INSTANCE_COMMANDS),
        "instance_type": {str(k): rows(v) for k, v in C.INSTANCE_TYPE_COMMANDS.items()},
        "device_special": rows(C.DEVICE_SPECIAL),
        "device_types": C.DEVICE_TYPE_NAMES,
        "instance_types": C.INSTANCE_TYPE_NAMES,
    }


def reply_json(r: Reply) -> dict[str, Any]:
    return {"status": r.status, "value": r.value, "responders": r.responders}


class WebApp:
    def __init__(self, sim: Simulator, *, examples_dir: Path | None = None,
                 loader: Callable[[Path], Simulator] | None = None, update_hz: float = 8.0) -> None:
        self.sim = sim
        self.examples_dir = examples_dir
        self.loader = loader
        self.update_hz = update_hz
        self.sockets: set[web.WebSocketResponse] = set()
        self._records: list[dict] = []
        self._job: asyncio.Task | None = None
        self._job_name = ""
        self._push_task: asyncio.Task | None = None
        self.app = web.Application(middlewares=[self._errors])
        self.app.add_routes([
            web.get("/", self.index),
            web.get("/api/info", self.info),
            web.get("/api/state", self.state),
            web.get("/api/history", self.history),
            web.get("/api/commands", self.commands),
            web.get("/api/examples", self.examples),
            web.post("/api/load", self.load),
            web.post("/api/send", self.send),
            web.post("/api/command", self.command),
            web.post("/api/act", self.act),
            web.post("/api/commission", self.commission),
            web.post("/api/scan", self.scan),
            web.post("/api/testbench", self.testbench),
            web.post("/api/job/cancel", self.cancel_job),
            web.get("/ws", self.ws),
            web.static("/static", STATIC),
        ])
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)
        self._attach(sim)

    # -- lifecycle -----------------------------------------------------------------
    def _attach(self, sim: Simulator) -> None:
        self.sim = sim
        sim.bus.listeners.append(self._on_record)

    async def _on_startup(self, app: web.Application) -> None:
        await self.sim.start()
        self._push_task = asyncio.create_task(self._push_loop())

    async def _on_cleanup(self, app: web.Application) -> None:
        if self._push_task:
            self._push_task.cancel()
        if self._job:
            self._job.cancel()
        await self.sim.stop()

    @web.middleware
    async def _errors(self, request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (KeyError, ValueError, TypeError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    def _on_record(self, rec: BusRecord) -> None:
        self._records.append(rec.to_dict())

    async def _push_loop(self) -> None:
        period = 1.0 / self.update_hz
        while True:
            await asyncio.sleep(period)
            if not self.sockets:
                self._records.clear()
                continue
            records, self._records = self._records, []
            msg = {"type": "state", "state": self.sim.snapshot(), "records": records[-500:],
                   "job": self._job_name if self._job and not self._job.done() else None}
            await self._broadcast(msg)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg)
        for ws in list(self.sockets):
            try:
                await ws.send_str(data)
            except Exception:
                self.sockets.discard(ws)

    # -- pages -------------------------------------------------------------------------
    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "index.html")

    async def info(self, request: web.Request) -> web.Response:
        return web.json_response({"version": __version__, "name": self.sim.name})

    async def state(self, request: web.Request) -> web.Response:
        return web.json_response(self.sim.snapshot())

    async def history(self, request: web.Request) -> web.Response:
        since = int(request.query.get("since", 0))
        recs = [r.to_dict() for r in self.sim.bus.history if r.seq > since]
        return web.json_response(recs[-2000:])

    async def commands(self, request: web.Request) -> web.Response:
        return web.json_response(command_catalog())

    async def examples(self, request: web.Request) -> web.Response:
        out = []
        if self.examples_dir and self.examples_dir.is_dir():
            out = sorted(p.stem for p in self.examples_dir.glob("*.yaml"))
        return web.json_response(out)

    async def load(self, request: web.Request) -> web.Response:
        body = await request.json()
        name = str(body["example"])
        if not self.examples_dir or not self.loader or "/" in name or ".." in name:
            raise ValueError("loading examples is not enabled")
        path = self.examples_dir / f"{name}.yaml"
        if not path.is_file():
            raise ValueError(f"no example {name!r}")
        new = self.loader(path)
        old = self.sim
        bridges = old.bridges
        old.bridges = []
        await old.stop()
        new.bridges = bridges
        for br in bridges:
            br.sim = new
        self._attach(new)
        await new.start()
        return web.json_response({"ok": True, "name": new.name})

    # -- transport selection -----------------------------------------------------------------
    def _transport(self, target: str | None) -> Any:
        if target in (None, "", "virtual"):
            return self.sim.bus
        for br in self.sim.bridges:
            if target in ("hardware", br.adapter.name):
                return br
        raise ValueError(f"no transport {target!r}")

    async def send(self, request: web.Request) -> web.Response:
        body = await request.json()
        frames = [parse_frame(f) for f in body["frames"]]
        tr = self._transport(body.get("via"))
        r = await tr.send(frames, source="web", expect_answer=body.get("expect_answer"))
        return web.json_response({"ok": True, **reply_json(r)})

    async def command(self, request: web.Request) -> web.Response:
        body = await request.json()
        frames = build_command(body)
        tr = self._transport(body.get("via"))
        r = await tr.send(frames, source="web")
        return web.json_response({"ok": True, "frames": [f.hex() for f in frames], **reply_json(r)})

    async def act(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = body.pop("action")
        return web.json_response(self.sim.act(action, **body))

    # -- jobs ------------------------------------------------------------------------------------
    def _start_job(self, name: str, coro: Any) -> None:
        if self._job and not self._job.done():
            raise ValueError(f"busy: {self._job_name}")
        self._job_name = name

        async def run() -> None:
            try:
                result = await coro
                await self._broadcast({"type": "job", "name": name, "done": True, "result": result})
            except asyncio.CancelledError:
                await self._broadcast({"type": "job", "name": name, "done": True, "cancelled": True})
                raise
            except Exception as exc:
                log.exception("job %s failed", name)
                await self._broadcast({"type": "job", "name": name, "done": True, "error": str(exc)})

        self._job = asyncio.create_task(run())

    async def _progress(self, info: dict) -> None:
        await self._broadcast({"type": "progress", **info})

    async def commission(self, request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        ctl = Controller(self._transport(body.get("via")), source="commissioning")

        async def job() -> Any:
            found = await ctl.commission(devices=bool(body.get("devices")), only_new=bool(body.get("only_new", True)),
                                         progress=self._progress)
            return [{"short_address": f.short_address, "random_address": f"{f.random_address:06X}", "kind": f.kind}
                    for f in found]

        self._start_job("commissioning", job())
        return web.json_response({"ok": True, "started": "commissioning"})

    async def scan(self, request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        ctl = Controller(self._transport(body.get("via")), source="scan")

        async def job() -> Any:
            res = await ctl.scan(details=bool(body.get("details", True)), progress=self._progress)
            return {"gear": {str(k): v for k, v in res.gear.items()},
                    "devices": {str(k): v for k, v in res.devices.items()}}

        self._start_job("scan", job())
        return web.json_response({"ok": True, "started": "scan"})

    async def testbench(self, request: web.Request) -> web.Response:
        from ..testbench import run_suite

        body = await request.json() if request.can_read_body else {}
        transport = self._transport(body.get("via"))

        async def job() -> Any:
            return await run_suite(transport, suite=str(body.get("suite", "gear")),
                                   addresses=body.get("addresses"), progress=self._progress)

        self._start_job("testbench", job())
        return web.json_response({"ok": True, "started": "testbench"})

    async def cancel_job(self, request: web.Request) -> web.Response:
        if self._job and not self._job.done():
            self._job.cancel()
        return web.json_response({"ok": True})

    # -- websocket ---------------------------------------------------------------------------------
    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self.sockets.add(ws)
        await ws.send_str(json.dumps({"type": "hello", "version": __version__,
                                      "history": [r.to_dict() for r in self.sim.bus.history[-300:]],
                                      "state": self.sim.snapshot()}))
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        body = json.loads(msg.data)
                        if body.get("type") == "act":
                            body.pop("type")
                            self.sim.act(body.pop("action"), **body)
                    except Exception as exc:
                        await ws.send_str(json.dumps({"type": "error", "error": str(exc)}))
        finally:
            self.sockets.discard(ws)
        return ws


def build_command(body: dict[str, Any]) -> list[Frame]:
    """Build the frames for a console command.

    ``body``: kind ("gear" | "gear_special" | "device" | "device_special"),
    target ("A5", "G1", "BC", or a device short address), opcode, optional
    dtr0/dtr1/dtr2, dt (device type), instance (for devices), data (for
    special commands), twice (default: as the command table says).
    """
    kind = body.get("kind", "gear")
    op = int(body["opcode"])
    frames: list[Frame] = []
    if kind in ("gear", "gear_special"):
        for reg, addr in (("dtr0", 0xA3), ("dtr1", 0xC3), ("dtr2", 0xC5)):
            if body.get(reg) not in (None, ""):
                frames.append(Frame.forward16(addr, int(body[reg])))
        if kind == "gear_special":
            cmd = C.GEAR_SPECIAL.get(op)
            f = Frame.forward16(op, int(body.get("data", 0)))
        else:
            dt = body.get("dt")
            if dt not in (None, ""):
                frames.append(Frame.forward16(0xC1, int(dt)))
                cmd = C.DEVICE_TYPE_COMMANDS.get(int(dt), {}).get(op)
            else:
                cmd = C.GEAR_COMMANDS.get(op)
            target = parse_gear_target(str(body.get("target", "BC")))
            if body.get("dapc"):
                target &= 0xFE
                cmd = None
            f = Frame.forward16(target, op)
    else:
        for reg, sub in (("dtr0", 0x30), ("dtr1", 0x31), ("dtr2", 0x32)):
            if body.get(reg) not in (None, ""):
                frames.append(Frame.forward24(0xC1, sub, int(body[reg])))
        if kind == "device_special":
            cmd = C.DEVICE_SPECIAL.get(op)
            f = Frame.forward24(0xC1, op, int(body.get("data", 0)))
        else:
            t = str(body.get("target", "BC")).upper()
            if t in ("BC", "ALL"):
                a = DeviceAddress.BROADCAST
            elif t.startswith("DG"):
                a = DeviceAddress.group(int(t[2:]))
            else:
                a = DeviceAddress.short(int(t.lstrip("AD")))
            inst = body.get("instance", "device")
            if inst in ("device", None, ""):
                ib = InstanceByte.DEVICE
                cmd = C.DEVICE_COMMANDS.get(op)
            elif inst == "all":
                ib = InstanceByte.ALL_INSTANCES
                cmd = C.INSTANCE_COMMANDS.get(op)
            else:
                ib = InstanceByte.number(int(inst))
                cmd = C.INSTANCE_COMMANDS.get(op)
            f = Frame.forward24(a, ib, op)
    frames.append(f)
    twice = body.get("twice")
    if twice is None:
        twice = bool(cmd and cmd.twice)
    if twice:
        if kind == "gear" and body.get("dt") not in (None, ""):
            frames.append(Frame.forward16(0xC1, int(body["dt"])))
        frames.append(f)
    return frames
