import pytest

from dali_sim.bus import Bus
from dali_sim.clock import ManualClock
from dali_sim.protocol.frames import Frame


@pytest.fixture
def bus():
    b = Bus(ManualClock())
    yield b
    assert not b.errors, "internal bus errors:\n" + "\n".join(b.errors)


def f16(a: int, b: int) -> Frame:
    return Frame.forward16(a, b)


def f24(a: int, b: int, c: int) -> Frame:
    return Frame.forward24(a, b, c)


def twice(bus: Bus, frame: Frame):
    return bus.request([frame, frame])
