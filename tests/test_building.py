"""Several DALI lines in one simulator."""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from dali_sim.building import Building
from dali_sim.config import ConfigError, build, build_building, load, load_building
from dali_sim.protocol.frames import Frame
from dali_sim.server.tcp import TcpServer
from dali_sim.server.web import WebApp

EXAMPLES = Path(__file__).parent.parent / "examples"


def two_lines(manual=True) -> Building:
    return build_building({
        "name": "Test building",
        "seed": 7,
        "lines": [
            {"name": "North", "gear": [{"name": "n{n}", "count": 3, "start_address": 0, "type": "led"}]},
            {"name": "South", "gear": [{"name": "s{n}", "count": 5, "start_address": 0, "type": "led"}]},
        ],
    }, manual_clock=manual)


def test_lines_are_separate():
    b = two_lines()
    north, south = b.line(1), b.line(2)
    assert (north.name, south.name) == ("North", "South")
    assert north.bus is not south.bus
    r = north.bus.request(Frame.forward16(0xFF, 0x00))  # broadcast OFF on line 1 only
    assert not r.answered
    t = south.bus.now()
    assert all(g.level_at(north.bus.now()) == 0 for g in north.gear)
    assert all(g.level_at(t) == 254 for g in south.gear)
    with pytest.raises(ValueError):
        b.line(3)


def test_example_three_lines():
    b = load_building(EXAMPLES / "three-lines.yaml", manual_clock=True)
    assert len(b) == 3
    assert [len(s.gear) for s in b.lines] == [64, 64, 64]
    assert [s.name for s in b.lines] == ["Ground floor", "First floor", "Second floor"]
    with pytest.raises(ConfigError):
        load(EXAMPLES / "three-lines.yaml")


def test_realtime_lines_share_one_clock():
    b = two_lines(manual=False)
    assert b.line(1).bus.clock is b.line(2).bus.clock
    b.line(1).speed = 5
    assert b.line(2).speed == 5


def test_single_line_file_is_a_one_line_building():
    b = build_building({"gear": [{"name": "g", "type": "led"}]}, manual_clock=True)
    assert len(b) == 1
    b.pad_to(3)
    assert len(b) == 3 and b.line(3).name == "Line 3 (empty)"


async def test_tcp_line_field_and_per_line_server():
    b = two_lines()
    server = TcpServer(b.line(1), "127.0.0.1", 0, lines=lambda: b.lines, line=1)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)

        async def call(req):
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            return json.loads(await reader.readline())

        assert (await call({"op": "hello"}))["lines"] == 2
        r1 = await call({"op": "send", "frames": ["0991"]})            # A4 on line 1: nobody
        r2 = await call({"op": "send", "frames": ["0991"], "line": 2})  # A4 on line 2: s5
        assert r1["status"] == "none" and r2["responders"] == ["s5"]
        assert (await call({"op": "send", "frames": ["0991"], "line": 9}))["ok"] is False
        writer.close()
    finally:
        await server.stop()


async def test_web_api_per_line():
    b = two_lines(manual=False)
    app = WebApp(b)
    async with TestClient(TestServer(app.app)) as client:
        info = await (await client.get("/api/info")).json()
        assert info["lines"] == 2
        lines = await (await client.get("/api/lines")).json()
        assert [line["gear"] for line in lines] == [3, 5]
        st = await (await client.get("/api/state?line=2")).json()
        assert st["name"] == "South"
        r = await (await client.post("/api/command", json={"kind": "gear", "target": "A4", "opcode": 0x91, "line": 2})).json()
        assert r["responders"] == ["s5"]
        r = await client.post("/api/command", json={"kind": "gear", "target": "A4", "opcode": 0x91, "line": 3})
        assert r.status == 400
        ws = await client.ws_connect("/ws?line=2")
        hello = await ws.receive_json()
        assert hello["type"] == "hello" and hello["line"] == 2 and hello["state"]["name"] == "South"
        await ws.send_json({"type": "select", "line": 1})
        msg = await ws.receive_json()
        while msg["type"] != "hello":
            msg = await ws.receive_json()
        assert msg["line"] == 1 and msg["state"]["name"] == "North"
        await ws.close()


def test_fake_sticks_differ_per_line():
    from dali_sim.server.uhid import FakeUsbStick

    b = two_lines()
    s1, s2 = FakeUsbStick(b.line(1), "tridonic", line=1), FakeUsbStick(b.line(2), "tridonic", line=2)
    assert s1.serial != s2.serial and s1.uniq != s2.uniq


def test_single_simulator_still_accepted_by_webapp():
    app = WebApp(build({"name": "solo"}, manual_clock=True))
    assert len(app.building) == 1 and app.sim.name == "solo"
