"""The GPIO adapter against a fake pigpio that plays waveforms onto a
simulated bus and plays the bus answers back as edges."""

import asyncio
from collections import namedtuple

from dali_sim.adapters.gpio import GpioAdapter
from dali_sim.bus import Bus
from dali_sim.clock import ManualClock
from dali_sim.controller import Controller
from dali_sim.gear import ControlGear
from dali_sim.protocol.frames import Frame
from dali_sim.protocol.manchester import decode_pulses, encode_pulses

Pulse = namedtuple("Pulse", "gpio_on gpio_off delay")


class _Cb:
    def cancel(self):
        pass


class FakePi:
    """Enough of pigpio for GpioAdapter.  rx is inverted like a typical PHY."""

    pulse_class = Pulse

    def __init__(self, bus: Bus, tx: int = 17, rx: int = 27):
        self.bus, self.tx, self.rx = bus, tx, rx
        self.tick = 1_000_000
        self.fn = None
        self.waves = {}
        self._pending = []

    def set_mode(self, *a):
        pass

    def write(self, *a):
        pass

    def set_watchdog(self, *a):
        pass

    def callback(self, gpio, edge, fn):
        self.fn = fn
        return _Cb()

    def wave_clear(self):
        self._pending = []

    def wave_add_generic(self, pulses):
        self._pending = list(pulses)

    def wave_create(self):
        wid = len(self.waves)
        self.waves[wid] = self._pending
        return wid

    def wave_delete(self, wid):
        self.waves.pop(wid, None)

    def wave_tx_busy(self):
        return 0

    def _play(self, bus_pulses):
        """Emit rx edges for bus-level pulses (rx pin is inverted)."""
        for level, dur in bus_pulses:
            self.fn(self.rx, 1 - level, self.tick)  # edge at the start of each pulse
            self.tick += int(dur)
        if bus_pulses and bus_pulses[-1][0] == 0:
            self.fn(self.rx, 0, self.tick)  # back to idle (bus high -> pin low)
        self.tick += 5000
        self.fn(self.rx, 2, self.tick)  # watchdog

    def wave_send_once(self, wid):
        pulses = self.waves[wid]
        bus_pulses = [(1 if p.gpio_on else 0, p.delay) for p in pulses[:-1]]  # tx not inverted
        self._play(bus_pulses)
        frame = decode_pulses(bus_pulses + [(1, 3000)])
        if frame.bits in (16, 24):
            reply = self.bus.deliver(frame, source="gpio")
            if reply.answered:
                self._play(encode_pulses(Frame.backward(reply.value)))


async def test_gpio_adapter_talks_to_bus():
    real = Bus(ManualClock())
    lamp = real.attach(ControlGear("lamp", short_address=4))
    adapter = GpioAdapter(pi=FakePi(real), tx_invert=0, rx_invert=1)
    await adapter.open()
    c = Controller(adapter)
    assert await c.query(4, 0x91) == 0xFF
    await c.dapc(4, 99)
    await asyncio.sleep(0)
    assert lamp.level_at(real.now()) == 99
    assert await c.query(4, 0xA0) == 99
    assert await c.query(5, 0x91) is None
    await adapter.close()
