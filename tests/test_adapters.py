"""Every hardware driver against an emulator of the interface, which is
backed by a simulated bus.  Checks the byte protocols end to end."""


import pytest

from dali_sim.adapters import AdapterError, create
from dali_sim.adapters.hasseb import HassebAdapter
from dali_sim.adapters.tridonic import TridonicAdapter
from dali_sim.bus import Bus
from dali_sim.clock import ManualClock
from dali_sim.config import build
from dali_sim.controller import Controller
from dali_sim.devices import ControlDevice, PushButton
from dali_sim.emulators import HassebEmulator, TridonicEmulator
from dali_sim.gear import ControlGear
from dali_sim.protocol.frames import Frame


def real_bus():
    bus = Bus(ManualClock())
    lamp = bus.attach(ControlGear("lamp", short_address=1))
    bus.attach(ControlDevice("btn", short_address=2, instances=[PushButton()]))
    return bus, lamp


async def exercise(adapter, bus, lamp, devices=True, framing=True):
    c = Controller(adapter)
    assert await c.query(1, 0x91) == 0xFF
    assert await c.query(9, 0x91) is None
    await c.set_fade_time(1, 0)
    await c.set_max_level(1, 200)  # send-twice through the interface
    assert lamp.max_level == 200
    await c.dapc(1, 150)
    assert lamp.level_at(bus.now()) == 150
    assert await c.query_actual_level(1) == 150
    if devices:
        assert await c.device_query(0x05, 0xFF, 0x35) == 1  # 24-bit frames
    if not framing:
        return
    # broadcast query with different answers -> framing error
    bus.attach(ControlGear("other", short_address=3))
    r = await c.gear_cmd("BC", 0xA0, answer=True)
    assert r.collision


async def test_hasseb():
    bus, lamp = real_bus()
    adapter = HassebAdapter(transport=HassebEmulator(bus))
    await adapter.open()
    assert adapter.firmware == "2.6"
    await exercise(adapter, bus, lamp, devices=False)
    with pytest.raises(AdapterError):
        await adapter.send(Frame.forward24(0x05, 0xFF, 0x35))


async def test_tridonic_and_sniffing():
    bus, lamp = real_bus()
    adapter = TridonicAdapter(transport=TridonicEmulator(bus))
    seen = []
    adapter.listeners.append(lambda o: seen.append(o))
    await adapter.open()
    assert adapter.firmware == "1.7" and adapter.serial == "12345678"
    await exercise(adapter, bus, lamp)
    # traffic from another controller on the real bus is reported
    bus.request(Frame.forward16(0xFF, 0x05))
    assert any(o.kind == "forward" and o.frame == Frame.forward16(0xFF, 0x05) and o.text != "sent" for o in seen)


@pytest.mark.parametrize("protocol,spec", [("atxled", "atxled"), ("luba", "lunatone"), ("dalisim", "serial")])
async def test_serial_adapters_over_pty(protocol, spec):
    pytest.importorskip("serial")
    from dali_sim.server.pty import PtyServer

    sim = build({"gear": [{"name": "lamp", "short_address": 1, "type": "plain"}],
                 "devices": [{"name": "btn", "short_address": 2, "instances": ["push_button"]}]}, manual_clock=True)
    pty = PtyServer(sim, link=None, protocol=protocol)
    path = await pty.start()
    try:
        adapter = create(f"{spec}:{path}")
        await adapter.open()
        # The AtxLED line protocol has no documented framing-error report.
        await exercise(adapter, sim.bus, sim.units["lamp"], framing=protocol != "atxled")
        await adapter.close()
    finally:
        await pty.stop()


async def test_daliserver_protocol():
    from dali_sim.server.daliserver import DaliServerProtocolServer

    sim = build({"gear": [{"name": "lamp", "short_address": 1, "type": "plain"}]}, manual_clock=True)
    server = DaliServerProtocolServer(sim, "127.0.0.1", 0)
    await server.start()
    try:
        adapter = create(f"daliserver:127.0.0.1:{server.port}")
        await adapter.open()
        await exercise(adapter, sim.bus, sim.units["lamp"], devices=False)
        await adapter.close()
    finally:
        await server.stop()


def test_create_specs():
    assert create("lunatone:/dev/hidraw7").name == "lunatone"
    assert type(create("lunatone:/dev/hidraw7")).__name__ == "TridonicAdapter"
    assert create("remote:example.org:6000").port == 6000
    assert create("daliserver:somehost").port == 55825
    assert create("gpio:tx=5,rx=6").tx == 5
