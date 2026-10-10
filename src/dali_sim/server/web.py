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
from ..building import Building
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
    """Serves one :class:`Building` (one or more DALI lines).  Every request
    and every browser tab works on one line, chosen with ``line`` (1-based,
    default 1)."""

    def __init__(self, building: Building | Simulator, *, examples_dir: Path | None = None,
                 loader: Callable[[Path], Building | Simulator] | None = None, update_hz: float = 8.0) -> None:
        self.examples_dir = examples_dir
        self.loader = loader
        self.update_hz = update_hz
        #: open WebSockets and the line each one shows
        self.sockets: dict[web.WebSocketResponse, int] = {}
        self._records: dict[int, list[dict]] = {}
        self._job: asyncio.Task | None = None
        self._job_name = ""
        self._push_task: asyncio.Task | None = None
        #: called with the new Building after a scenario is loaded
        self.on_reload: list[Callable[[Building], None]] = []
        self.app = web.Application(middlewares=[self._errors])
        self.app.add_routes([
            web.get("/", self.index),
            web.get("/api/info", self.info),
            web.get("/api/lines", self.lines),
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
        self.building = building if isinstance(building, Building) else Building([building])
        self._attach(self.building)

    @property
    def sim(self) -> Simulator:
        """Line 1 (kept for single-line callers)."""
        return self.building.lines[0]

    # -- lifecycle -----------------------------------------------------------------
    def _attach(self, building: Building) -> None:
        self.building = building
        self._records = {}
        for n, sim in enumerate(building.lines, 1):
            self._records[n] = []
            sim.bus.listeners.append(lambda rec, n=n: self._on_record(n, rec))

    async def _on_startup(self, app: web.Application) -> None:
        await self.building.start()
        self._push_task = asyncio.create_task(self._push_loop())

    async def _on_cleanup(self, app: web.Application) -> None:
        if self._push_task:
            self._push_task.cancel()
        if self._job:
            self._job.cancel()
        await self.building.stop()

    @web.middleware
    async def _errors(self, request: web.Request, handler: Any) -> web.StreamResponse:
        try:
            return await handler(request)
        except web.HTTPException:
            raise
        except (KeyError, ValueError, TypeError) as exc:
            return web.json_response({"ok": False, "error": str(exc)}, status=400)

    def _on_record(self, line: int, rec: BusRecord) -> None:
        buf = self._records.get(line)
        if buf is not None:
            buf.append(rec.to_dict())
            if len(buf) > 2000:
                del buf[:-1000]

    def _line_no(self, value: Any) -> int:
        n = 1 if value in (None, "") else int(value)
        self.building.line(n)  # validates
        return n

    def _sim(self, value: Any) -> Simulator:
        return self.building.line(self._line_no(value))

    def _state_msg(self, line: int, records: list[dict]) -> dict[str, Any]:
        return {"type": "state", "line": line, "lines": self.building.summary(), "building": self.building.name,
                "state": self.building.line(line).snapshot(), "records": records[-500:],
                "job": self._job_name if self._job and not self._job.done() else None}

    async def _push_loop(self) -> None:
        period = 1.0 / self.update_hz
        while True:
            await asyncio.sleep(period)
            records = {n: buf[:] for n, buf in self._records.items()}
            for buf in self._records.values():
                buf.clear()
            if not self.sockets:
                continue
            cache: dict[int, str] = {}
            for ws, line in list(self.sockets.items()):
                if line > len(self.building):
                    line = self.sockets[ws] = 1
                if line not in cache:
                    cache[line] = json.dumps(self._state_msg(line, records.get(line, [])))
                await self._send(ws, cache[line])

    async def _send(self, ws: web.WebSocketResponse, data: str) -> None:
        try:
            await ws.send_str(data)
        except Exception:
            self.sockets.pop(ws, None)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg)
        for ws in list(self.sockets):
            await self._send(ws, data)

    # -- pages -------------------------------------------------------------------------
    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(STATIC / "index.html")

    async def info(self, request: web.Request) -> web.Response:
        return web.json_response({"version": __version__, "name": self.building.name, "lines": len(self.building)})

    async def lines(self, request: web.Request) -> web.Response:
        return web.json_response(self.building.summary())

    async def state(self, request: web.Request) -> web.Response:
        return web.json_response(self._sim(request.query.get("line")).snapshot())

    async def history(self, request: web.Request) -> web.Response:
        since = int(request.query.get("since", 0))
        sim = self._sim(request.query.get("line"))
        recs = [r.to_dict() for r in sim.bus.history if r.seq > since]
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
        if not isinstance(new, Building):
            new = Building([new])
        old = self.building
        # Keep at least as many lines as before: ports, sticks and bridges exist per line.
        new.pad_to(len(old))
        for old_sim, new_sim in zip(old.lines, new.lines):
            new_sim.bridges, old_sim.bridges = old_sim.bridges, []
            for br in new_sim.bridges:
                br.sim = new_sim
        await old.stop()
        self._attach(new)
        await new.start()
        for fn in self.on_reload:
            fn(new)
        return web.json_response({"ok": True, "name": new.name, "lines": len(new)})

    # -- transport selection -----------------------------------------------------------------
    def _transport(self, body: dict[str, Any]) -> Any:
        sim = self._sim(body.get("line"))
        target = body.get("via")
        if target in (None, "", "virtual"):
            return sim.bus
        for br in sim.bridges:
            if target in ("hardware", br.adapter.name):
                return br
        raise ValueError(f"no transport {target!r} on this line")

    async def send(self, request: web.Request) -> web.Response:
        body = await request.json()
        frames = [parse_frame(f) for f in body["frames"]]
        tr = self._transport(body)
        r = await tr.send(frames, source="web", expect_answer=body.get("expect_answer"))
        return web.json_response({"ok": True, **reply_json(r)})

    async def command(self, request: web.Request) -> web.Response:
        body = await request.json()
        frames = build_command(body)
        tr = self._transport(body)
        r = await tr.send(frames, source="web")
        return web.json_response({"ok": True, "frames": [f.hex() for f in frames], **reply_json(r)})

    async def act(self, request: web.Request) -> web.Response:
        body = await request.json()
        action = body.pop("action")
        sim = self._sim(body.pop("line", None))
        return web.json_response(sim.act(action, **body))

    # -- jobs ------------------------------------------------------------------------------------
    def _start_job(self, name: str, coro: Any) -> None:
        if self._job and not self._job.done():
            coro.close()
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

    def _progress(self, line: int) -> Callable[[dict], Any]:
        async def report(info: dict) -> None:
            await self._broadcast({"type": "progress", "line": line, **info})

        return report

    async def _job_body(self, request: web.Request) -> tuple[dict[str, Any], int]:
        body = await request.json() if request.can_read_body else {}
        return body, self._line_no(body.get("line"))

    async def commission(self, request: web.Request) -> web.Response:
        body, line = await self._job_body(request)
        ctl = Controller(self._transport(body), source="commissioning")

        async def job() -> Any:
            found = await ctl.commission(devices=bool(body.get("devices")), only_new=bool(body.get("only_new", True)),
                                         progress=self._progress(line))
            return [{"short_address": f.short_address, "random_address": f"{f.random_address:06X}", "kind": f.kind}
                    for f in found]

        self._start_job("commissioning", job())
        return web.json_response({"ok": True, "started": "commissioning", "line": line})

    async def scan(self, request: web.Request) -> web.Response:
        body, line = await self._job_body(request)
        ctl = Controller(self._transport(body), source="scan")

        async def job() -> Any:
            res = await ctl.scan(details=bool(body.get("details", True)), progress=self._progress(line))
            return {"gear": {str(k): v for k, v in res.gear.items()},
                    "devices": {str(k): v for k, v in res.devices.items()}}

        self._start_job("scan", job())
        return web.json_response({"ok": True, "started": "scan", "line": line})

    async def testbench(self, request: web.Request) -> web.Response:
        from ..testbench import run_suite

        body, line = await self._job_body(request)
        transport = self._transport(body)

        async def job() -> Any:
            return await run_suite(transport, suite=str(body.get("suite", "gear")),
                                   addresses=body.get("addresses"), progress=self._progress(line))

        self._start_job("testbench", job())
        return web.json_response({"ok": True, "started": "testbench", "line": line})

    async def cancel_job(self, request: web.Request) -> web.Response:
        if self._job and not self._job.done():
            self._job.cancel()
        return web.json_response({"ok": True})

    # -- websocket ---------------------------------------------------------------------------------
    async def _hello(self, ws: web.WebSocketResponse, line: int) -> None:
        sim = self.building.line(line)
        msg = self._state_msg(line, [])
        msg.update(type="hello", version=__version__, history=[r.to_dict() for r in sim.bus.history[-300:]])
        await ws.send_str(json.dumps(msg))

    async def ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        try:
            line = self._line_no(request.query.get("line"))
        except ValueError:
            line = 1
        self.sockets[ws] = line
        await self._hello(ws, line)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    body = json.loads(msg.data)
                    kind = body.pop("type", None)
                    if kind == "select":
                        line = self._line_no(body.get("line"))
                        self.sockets[ws] = line
                        await self._hello(ws, line)
                    elif kind == "act":
                        sim = self._sim(body.pop("line", self.sockets.get(ws, 1)))
                        sim.act(body.pop("action"), **body)
                except Exception as exc:
                    await ws.send_str(json.dumps({"type": "error", "error": str(exc)}))
        finally:
            self.sockets.pop(ws, None)
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
