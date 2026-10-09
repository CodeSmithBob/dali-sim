from dali_sim.common import MASK
from dali_sim.controller import Controller
from dali_sim.devices import ControlDevice, PushButton
from dali_sim.gear import ControlGear, LedExtension


async def test_commission_64_gear(bus):
    gear = [bus.attach(ControlGear(f"g{i}", seed=i, extensions=[LedExtension()])) for i in range(64)]
    found = await Controller(bus).commission()
    assert len(found) == 64
    assert sorted(g.short_address for g in gear) == list(range(64))
    # every unit answers on its own address
    for a in (0, 31, 63):
        r = await Controller(bus).gear_cmd(a, 0x91, answer=True)
        assert r.answered and len(r.responders) == 1


async def test_commission_keeps_existing_addresses(bus):
    a = bus.attach(ControlGear("a", short_address=0, seed=1))
    b = bus.attach(ControlGear("b", seed=2))
    c = bus.attach(ControlGear("c", seed=3))
    found = await Controller(bus).commission(only_new=True)
    assert a.short_address == 0
    assert {b.short_address, c.short_address} == {1, 2}
    assert len(found) == 2


async def test_commission_devices(bus):
    devs = [bus.attach(ControlDevice(f"d{i}", seed=100 + i, instances=[PushButton()])) for i in range(12)]
    gear = bus.attach(ControlGear("g", seed=5))
    found = await Controller(bus).commission(devices=True)
    assert len(found) == 12
    assert sorted(d.short_address for d in devs) == list(range(12))
    assert gear.short_address == MASK  # gear untouched by 24-bit commissioning


async def test_scan(bus):
    bus.attach(ControlGear("g", short_address=9, extensions=[LedExtension()]))
    bus.attach(ControlDevice("d", short_address=4, instances=[PushButton(), PushButton()]))
    res = await Controller(bus).scan()
    assert list(res.gear) == [9]
    assert res.gear[9]["device_type"] == 6
    assert res.gear[9]["gtin"] == 0x0000_1234_5678
    assert res.devices[4]["instances"] == [{"number": 0, "type": 1}, {"number": 1, "type": 1}]


async def test_duplicate_short_address_detected(bus):
    from dali_sim.testbench import run_suite

    bus.attach(ControlGear("a", short_address=5, random_address=0x111111))
    bus.attach(ControlGear("b", short_address=5, random_address=0x222222))
    res = await run_suite(bus, suite="bus")
    fails = [r for r in res["results"] if not r["passed"]]
    assert any("used twice" in r["test"] for r in fails)
