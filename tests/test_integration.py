import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dali_sim.adapters.simulated import LoopbackAdapter, RemoteAdapter
from dali_sim.bridge import Bridge
from dali_sim.bus import Bus
from dali_sim.clock import ManualClock
from dali_sim.config import build, load
from dali_sim.controller import Controller
from dali_sim.gear import ControlGear
from dali_sim.protocol.frames import Frame
from dali_sim.server.tcp import TcpServer
from dali_sim.server.web import WebApp, build_command
from dali_sim.testbench import run_suite

EXAMPLES = Path(__file__).parent.parent / "examples"


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*.yaml")), ids=lambda p: p.stem)
def test_examples_load(path):
    from dali_sim.config import load_building

    for sim in load_building(path, manual_clock=True).lines:
        assert sim.gear or sim.devices
        sim.snapshot()


async def test_testbench_passes_on_office():
    sim = load(EXAMPLES / "office-16.yaml", manual_clock=True)
    res = await run_suite(sim.bus, suite="all", addresses=None)
    assert res["failed"] == 0, [r for r in res["results"] if not r["passed"]]
    assert res["passed"] > 100


class SloppyGear(ControlGear):
    """Executes configuration commands sent only once (a common bug)."""

    def on_frame(self, frame, t, source):
        out = super().on_frame(frame, t, source)
        out2 = super().on_frame(frame, t, source)  # pretend it was received twice
        return out if out is not None else out2


async def test_testbench_catches_send_twice_bug():
    bus = Bus(ManualClock())
    bus.attach(SloppyGear("bad", short_address=0))
    res = await run_suite(bus, suite="gear", addresses=[0], fade_check=False)
    failed = {r["test"] for r in res["results"] if not r["passed"]}
    assert "config command sent once is ignored" in failed


async def test_tcp_server_and_remote_adapter():
    sim = build({"gear": [{"name": "g", "short_address": 1, "type": "led"}]}, manual_clock=True)
    server = TcpServer(sim, "127.0.0.1", 0)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        writer.write(b'{"id": 1, "op": "send", "frames": ["0391"]}\n')
        await writer.drain()
        resp = json.loads(await reader.readline())
        assert resp == {"ok": True, "status": "answer", "responders": ["g"], "value": 255, "id": 1}
        writer.write(b'{"id": 2, "op": "send", "frames": ["zz"]}\n')
        await writer.drain()
        assert json.loads(await reader.readline())["ok"] is False
        writer.close()

        remote = RemoteAdapter("127.0.0.1", server.port)
        await remote.open()
        r = await Controller(remote).query(1, 0xA0)
        assert r == 254
        await remote.close()
    finally:
        await server.stop()


async def test_bridge_controller_mode_drives_real_bus():
    real = Bus(ManualClock())
    lamp = real.attach(ControlGear("real-lamp", short_address=3))
    sim = build({"name": "local"}, manual_clock=True)
    bridge = Bridge(sim, LoopbackAdapter(real), mode="controller")
    sim.bridges.append(bridge)
    await bridge.start()
    await Controller(bridge).dapc(3, 100)
    assert lamp.level_at(real.now()) == 100
    assert await Controller(bridge).query(3, 0xA0) == 100
    assert any("[hw]" in r.text for r in sim.bus.history)


async def test_bridge_devices_mode_answers_real_controller():
    real = Bus(ManualClock())
    adapter = LoopbackAdapter(real)
    sim = build({"gear": [{"name": "virtual", "short_address": 7, "type": "led"}]}, manual_clock=True)
    bridge = Bridge(sim, adapter, mode="devices")
    await bridge.start()
    # a "real" controller on the real bus switches the virtual lamp
    real.request(Frame.forward16(0x0E, 50))
    assert sim.units["virtual"].level_at(sim.bus.now()) == 50
    real.request(Frame.forward16(0x0F, 0xA0))
    await asyncio.sleep(0.01)
    assert bridge.answered == 1


async def test_bridge_hybrid_merges_answers():
    real = Bus(ManualClock())
    real.attach(ControlGear("real", short_address=1))
    sim = build({"gear": [{"name": "virt", "short_address": 2, "type": "led"}]}, manual_clock=True)
    bridge = Bridge(sim, LoopbackAdapter(real), mode="hybrid")
    await bridge.start()
    res = await Controller(bridge).scan(details=False)
    assert sorted(res.gear) == [1, 2]
    r = await Controller(bridge).gear_cmd("BC", 0xA0, answer=True)
    assert r.answered  # both at 254


def test_build_command():
    frames = build_command({"kind": "gear", "target": "A1", "opcode": 0x2E, "dtr0": 4})
    assert [f.hex() for f in frames] == ["A3 04", "03 2E", "03 2E"]
    frames = build_command({"kind": "gear", "target": "G0", "opcode": 0xE7, "dt": 8, "dtr0": 250, "dtr1": 0})
    assert [f.hex() for f in frames] == ["A3 FA", "C3 00", "C1 08", "81 E7"]
    frames = build_command({"kind": "device", "target": "3", "instance": "0", "opcode": 0x80})
    assert [f.hex() for f in frames] == ["07 00 80"]
    frames = build_command({"kind": "gear", "target": "BC", "dapc": True, "opcode": 100})
    assert [f.hex() for f in frames] == ["FE 64"]


async def test_web_api():
    sim = load(EXAMPLES / "office-16.yaml")
    app = WebApp(sim, examples_dir=EXAMPLES, loader=lambda p: load(p))
    async with TestClient(TestServer(app.app)) as client:
        r = await client.get("/api/state")
        state = await r.json()
        assert len(state["gear"]) == 16
        r = await client.post("/api/command", json={"kind": "gear", "target": "A0", "opcode": 0x91})
        body = await r.json()
        assert body["status"] == "answer"
        r = await client.post("/api/act", json={"action": "click", "device": "R1 switch", "instance": 0})
        assert (await r.json())["ok"]
        r = await client.post("/api/act", json={"action": "nope"})
        assert r.status == 400
        r = await client.get("/api/commands")
        assert "gear" in await r.json()
        r = await client.get("/")
        assert "dali-sim" in await r.text()
        r = await client.post("/api/load", json={"example": "../etc/passwd"})
        assert r.status == 400
        r = await client.post("/api/load", json={"example": "colour-lab"})
        assert (await r.json())["ok"]
        r = await client.get("/api/state")
        assert (await r.json())["name"] == "Colour lab (DT8)"


async def test_pty_and_serial_adapter():
    pytest.importorskip("serial")
    from dali_sim.adapters.serial_line import SerialLineAdapter
    from dali_sim.server.pty import PtyServer

    sim = build({"gear": [{"name": "g", "short_address": 2, "type": "led"}]}, manual_clock=True)
    pty = PtyServer(sim, link=None)
    path = await pty.start()
    try:
        adapter = SerialLineAdapter(path)
        await adapter.open()
        assert adapter.caps.forward24 and adapter.caps.sniff and not adapter.caps.send_backward
        assert await Controller(adapter).query(2, 0xA0) == 254
        await Controller(adapter).dapc(2, 77)
        assert sim.units["g"].level_at(sim.bus.now()) == 77
        await adapter.close()
    finally:
        await pty.stop()
