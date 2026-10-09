import pytest

from conftest import f16, twice
from dali_sim.common import MASK
from dali_sim.gear import ColourExtension, ControlGear, EmergencyGear, LedExtension
from dali_sim.protocol.dimming import log_curve_percent


@pytest.fixture
def gear(bus):
    g = bus.attach(ControlGear("g", short_address=3, extensions=[LedExtension()], physical_min_level=10))
    return g


def q(bus, op, a=0x07):
    r = bus.request(f16(a, op))
    return r.value if r.answered else None


def test_present_and_reset_values(bus, gear):
    assert q(bus, 0x91) == 0xFF
    assert q(bus, 0xA0) == 254
    assert q(bus, 0xA2) == 10  # min level = physical min
    assert q(bus, 0xA5) == 0x07
    assert q(bus, 0x99) == 6
    assert q(bus, 0x97) == 0x08  # DALI-2 version 2.0


def test_not_addressed(bus, gear):
    assert not bus.request(f16(0x09, 0x91)).answered  # A4
    assert bus.request(f16(0xFF, 0x91)).answered


def test_send_twice_rule(bus, gear):
    bus.request(f16(0xA3, 100))
    bus.request(f16(0x07, 0x2A))  # SET MAX LEVEL once: ignored
    assert q(bus, 0xA1) == 254
    bus.request(f16(0xA3, 100))
    twice(bus, f16(0x07, 0x2A))
    assert q(bus, 0xA1) == 100


def test_send_twice_too_slow_is_ignored(bus, gear):
    bus.request(f16(0xA3, 100))
    bus.request(f16(0x07, 0x2A))
    bus.advance(0.2)
    bus.request(f16(0x07, 0x2A))
    assert q(bus, 0xA1) == 254


def test_send_twice_with_frame_between_is_ignored(bus, gear):
    bus.request(f16(0xA3, 100))
    bus.request([f16(0x07, 0x2A), f16(0x07, 0x91), f16(0x07, 0x2A)])
    assert q(bus, 0xA1) == 254


def test_dapc_with_fade(bus, gear):
    bus.request(f16(0xA3, 4))
    twice(bus, f16(0x07, 0x2E))  # fade time 4 = 2.0 s
    bus.request(f16(0x06, 100))  # DAPC 100 on A3
    assert gear.fading_at(bus.now())
    assert q(bus, 0x90) & 0x10  # fade running
    bus.advance(1.0)
    mid = gear.level_at(bus.now())
    assert 100 < mid < 254
    bus.advance(1.2)
    assert q(bus, 0xA0) == 100
    assert not q(bus, 0x90) & 0x10


def test_off_and_on_from_off_starts_at_min(bus, gear):
    bus.request(f16(0x07, 0x00))
    assert q(bus, 0xA0) == 0
    assert q(bus, 0x93) is None  # lamp power on: NO
    bus.request(f16(0xA3, 3))
    twice(bus, f16(0x07, 0x2E))
    bus.request(f16(0x06, 200))
    assert gear.level_at(bus.now()) <= 20


def test_limits_and_limit_error(bus, gear):
    bus.request(f16(0xA3, 50))
    twice(bus, f16(0x07, 0x2B))  # SET MIN LEVEL 50
    bus.request(f16(0x06, 20))
    assert q(bus, 0xA0) == 50
    assert q(bus, 0x94) == 0xFF
    bus.request(f16(0x06, 120))
    assert q(bus, 0x94) is None


def test_step_commands(bus, gear):
    bus.request(f16(0x06, 100))
    bus.request(f16(0x07, 0x03))
    assert q(bus, 0xA0) == 101
    bus.request(f16(0x07, 0x04))
    assert q(bus, 0xA0) == 100
    bus.request(f16(0x07, 0x06))  # RECALL MIN
    bus.request(f16(0x07, 0x07))  # STEP DOWN AND OFF at min -> off
    assert q(bus, 0xA0) == 0
    bus.request(f16(0x07, 0x08))  # ON AND STEP UP from off -> min
    assert q(bus, 0xA0) == 10


def test_up_does_not_switch_on(bus, gear):
    bus.request(f16(0x07, 0x00))
    bus.request(f16(0x07, 0x01))
    bus.advance(0.5)
    assert q(bus, 0xA0) == 0


def test_groups_and_scenes(bus, gear):
    twice(bus, f16(0x07, 0x62))  # ADD TO GROUP 2
    assert q(bus, 0xC0) == 0x04
    bus.request(f16(0x84, 77))  # DAPC to G2
    assert q(bus, 0xA0) == 77
    bus.request(f16(0xA3, 123))
    twice(bus, f16(0x07, 0x45))  # SET SCENE 5
    assert q(bus, 0xB5) == 123
    bus.request(f16(0x85, 0x15))  # G2 GO TO SCENE 5
    assert q(bus, 0xA0) == 123
    twice(bus, f16(0x07, 0x55))  # REMOVE FROM SCENE 5
    assert q(bus, 0xB5) == MASK
    twice(bus, f16(0x07, 0x72))  # REMOVE FROM GROUP 2
    assert q(bus, 0xC0) == 0


def test_reset(bus, gear):
    bus.request(f16(0xA3, 9))
    twice(bus, f16(0x07, 0x2E))
    assert q(bus, 0x95) is None
    twice(bus, f16(0x07, 0x20))
    assert q(bus, 0x95) == 0xFF
    assert q(bus, 0xA5) == 0x07


def test_dtr_queries(bus, gear):
    bus.request([f16(0xA3, 1), f16(0xC3, 2), f16(0xC5, 3)])
    assert (q(bus, 0x98), q(bus, 0x9C), q(bus, 0x9D)) == (1, 2, 3)
    bus.request(f16(0x06, 150))
    twice(bus, f16(0x07, 0x21))  # STORE ACTUAL LEVEL IN DTR0
    assert q(bus, 0x98) == 150


def test_short_address_change(bus, gear):
    bus.request(f16(0xA3, (20 << 1) | 1))
    twice(bus, f16(0x07, 0x80))
    assert gear.short_address == 20
    bus.request(f16(0xA3, 0xFF))
    twice(bus, f16(0x29, 0x80))
    assert gear.short_address == MASK
    assert bus.request(f16(0xFD, 0x91)).answered  # broadcast unaddressed


def test_memory_bank_read_and_write(bus, gear):
    bus.request([f16(0xC3, 0), f16(0xA3, 0)])
    assert q(bus, 0xC5) == 0x1A  # last accessible location of bank 0
    assert q(bus, 0x98) == 1  # DTR0 incremented
    # bank 1 is locked until 0x55 is written to the lock byte
    twice(bus, f16(0x07, 0x81))  # ENABLE WRITE MEMORY
    bus.request([f16(0xC3, 1), f16(0xA3, 0x05)])
    assert not bus.request(f16(0xC7, 0x42)).answered
    bus.request(f16(0xA3, 0x02))
    assert bus.request(f16(0xC7, 0x55)).value == 0x55
    bus.request(f16(0xA3, 0x05))
    assert bus.request(f16(0xC7, 0x42)).value == 0x42
    bus.request([f16(0xC3, 1), f16(0xA3, 0x05)])
    assert q(bus, 0xC5) == 0x42


def test_write_memory_needs_enable(bus, gear):
    bus.request([f16(0xC3, 1), f16(0xA3, 0x02)])
    assert not bus.request(f16(0xC7, 0x55)).answered


def test_system_failure_and_power_on(bus, gear):
    bus.request(f16(0xA3, 40))
    twice(bus, f16(0x07, 0x2C))  # system failure level 40
    bus.request(f16(0xA3, 80))
    twice(bus, f16(0x07, 0x2D))  # power on level 80
    bus.set_power(False)
    bus.advance(0.6)
    assert gear.level_at(bus.now()) == 40
    bus.set_power(True)
    gear.set_mains(False)
    assert gear.light_percent_at(bus.now()) == 0
    gear.set_mains(True)
    assert gear.level_at(bus.now()) == 80
    assert q(bus, 0x9B) == 0xFF  # power failure / power cycle seen


def test_multiple_device_types():
    from dali_sim.bus import Bus

    bus = Bus()
    bus.attach(ControlGear("g", short_address=0, extensions=[LedExtension(), ColourExtension()]))
    assert q(bus, 0x99, a=0x01) == MASK
    assert [q(bus, 0xA7, a=0x01) for _ in range(3)] == [6, 8, 254]


def test_dt6_linear_curve(bus, gear):
    bus.request(f16(0xA3, 1))
    bus.request([f16(0xC1, 6), f16(0x07, 0xE3), f16(0xC1, 6), f16(0x07, 0xE3)])
    assert bus.request([f16(0xC1, 6), f16(0x07, 0xEE)]).value == 1
    bus.request(f16(0x06, 127))
    assert gear.light_percent_at(bus.now()) == pytest.approx(50.0, abs=0.01)
    # without ENABLE DEVICE TYPE the extended command is ignored
    assert not bus.request(f16(0x07, 0xEE)).answered


def test_dt6_send_twice_with_single_enable(bus, gear):
    bus.request(f16(0xA3, 1))
    bus.request([f16(0xC1, 6), f16(0x07, 0xE3), f16(0x07, 0xE3)])
    assert bus.request([f16(0xC1, 6), f16(0x07, 0xEE)]).value == 1


def test_dt8_colour_temperature(bus):
    g = bus.attach(ControlGear("tw", short_address=1, extensions=[LedExtension(), ColourExtension()]))
    mirek = 250  # 4000 K
    bus.request([f16(0xA3, mirek & 0xFF), f16(0xC3, mirek >> 8), f16(0xC1, 8), f16(0x03, 0xE7)])
    bus.request([f16(0xC1, 8), f16(0x03, 0xE2)])  # ACTIVATE
    bus.request(f16(0xA3, 2))
    r = bus.request([f16(0xC1, 8), f16(0x03, 0xFA)])  # QUERY COLOUR VALUE Tc
    assert r.value == 0
    assert bus.request(f16(0x03, 0x98)).value == 250  # LSB in DTR0
    status = bus.request([f16(0xC1, 8), f16(0x03, 0xF8)]).value
    assert status & 0x20  # Tc active
    features = bus.request([f16(0xC1, 8), f16(0x03, 0xF9)]).value
    assert features & 0x02
    assert g.snapshot(bus.now())["dt8"]["tc_kelvin"] == 4000


def test_dt8_rgb_and_scene_colour(bus):
    g = bus.attach(ControlGear("rgb", short_address=2, extensions=[ColourExtension(tc=False, rgbwaf_channels=3)]))
    bus.request([f16(0xA3, 254), f16(0xC3, 0), f16(0xC5, 0), f16(0xC1, 8), f16(0x05, 0xEB)])
    bus.request(f16(0xA3, 200))
    twice(bus, f16(0x05, 0x41))  # SET SCENE 1 also stores the temporary colour (red)
    bus.request([f16(0xA3, 0), f16(0xC3, 0), f16(0xC5, 254), f16(0xC1, 8), f16(0x05, 0xEB), f16(0xC1, 8),
                 f16(0x05, 0xE2)])  # blue now
    assert g.snapshot(bus.now())["dt8"]["rgb"] == "#0000ff"
    bus.request(f16(0x05, 0x11))  # GO TO SCENE 1
    bus.advance(0.1)
    assert g.snapshot(bus.now())["dt8"]["rgb"] == "#ff0000"


def test_dt1_emergency(bus):
    g = bus.attach(EmergencyGear("em", short_address=4))
    assert bus.request(f16(0x09, 0x99)).value == 1
    g.set_mains(False)
    assert bus.request(f16(0x09, 0x91)).answered  # still on the bus
    mode = bus.request([f16(0xC1, 1), f16(0x09, 0xFA)]).value
    assert mode & 0b100  # emergency mode
    assert g.light_percent_at(bus.now()) == pytest.approx(log_curve_percent(254))
    bus.advance(600)
    charge = bus.request([f16(0xC1, 1), f16(0x09, 0xF1)]).value
    assert charge < 254
    g.set_mains(True)
    bus.request([f16(0xC1, 1), f16(0x09, 0xE3)])  # START FUNCTION TEST
    assert bus.request([f16(0xC1, 1), f16(0x09, 0xFA)]).value & 0x10
    bus.advance(31)
    status = bus.request([f16(0xC1, 1), f16(0x09, 0xFD)]).value
    assert status & 0b10  # function test done


def test_identify(bus, gear):
    twice(bus, f16(0x07, 0x25))
    assert gear.is_identifying
    bus.advance(11)
    assert not gear.is_identifying
