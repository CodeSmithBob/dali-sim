# Using dali-sim from Python

## A bus in a few lines

```python
from dali_sim import Bus, Frame
from dali_sim.gear import ControlGear, LedExtension

bus = Bus()  # manual clock: deterministic, as fast as Python runs
lamp = bus.attach(ControlGear("lamp", short_address=0, extensions=[LedExtension()]))

reply = bus.request(Frame.forward16(0x01, 0xA0))   # A0: QUERY ACTUAL LEVEL
print(reply.value)                                 # 254

bus.request([Frame.forward16(0xA3, 4), Frame.forward16(0x01, 0x2E), Frame.forward16(0x01, 0x2E)])
bus.request(Frame.forward16(0x00, 100))            # DAPC 100 with fade time 4 (2 s)
bus.advance(1.0)
print(lamp.level_at(bus.now()))                    # halfway
```

## High-level controller (async)

```python
import asyncio
from dali_sim import Bus, Controller
from dali_sim.gear import ControlGear

async def main():
    bus = Bus()
    for i in range(10):
        bus.attach(ControlGear(f"g{i}", seed=i))
    c = Controller(bus)
    found = await c.commission()          # random-address binary search
    await c.add_to_group(0, 3)
    await c.dapc("G3", 200)
    print(await c.query_actual_level(0))
    print((await c.scan()).gear.keys())

asyncio.run(main())
```

The same `Controller` works with a hardware adapter:

```python
from dali_sim.adapters import create

async def real():
    async with create("hasseb") as adapter:
        c = Controller(adapter)
        print(await c.scan(details=False))
```

## Loading a YAML scenario

```python
from dali_sim.config import load

sim = load("examples/office-16.yaml", manual_clock=True)
sim.world.start(sim)
sim.act("click", device="R1 switch", instance=0)
sim.bus.advance(2)
print([g.level_at(sim.bus.now()) for g in sim.gear][:4])
```

## Writing tests for your own controller

Use the simulator as a test fixture: build a bus, run your controller code against `bus.send(...)` (or the TCP API), then assert on unit state (`gear.level_at(t)`, `gear.groups`, `device.instances[0].event_filter`) or on `bus.history`.
