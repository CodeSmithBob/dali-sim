import math

import pytest

from dali_sim.protocol import timing as T
from dali_sim.protocol.address import AddrKind, DeviceAddress, GearAddress, InstanceByte, InstKind, parse_gear_target
from dali_sim.protocol.commands import DEVICE_COMMANDS, GEAR_COMMANDS, GEAR_SPECIAL, find_by_name
from dali_sim.protocol.decode import FrameDecoder, decode
from dali_sim.protocol.dimming import (
    extended_fade_seconds,
    fade_rate_steps_per_second,
    fade_time_seconds,
    log_curve_percent,
    percent_to_log_level,
)
from dali_sim.protocol.events import Event, EventScheme
from dali_sim.protocol.frames import Frame, ManchesterError, decode_halfbits, encode_halfbits, resolve_collision
from dali_sim.protocol.manchester import decode_pulses, encode_pulses


@pytest.mark.parametrize("bits,value", [(8, 0x00), (8, 0xFF), (8, 0xA5), (16, 0xFF90), (16, 0x0001), (24, 0xC10100),
                                        (24, 0x000000), (24, 0xFFFFFF)])
def test_manchester_roundtrip(bits, value):
    f = Frame(bits, value)
    assert decode_halfbits(encode_halfbits(f)) == f


def test_start_bit_and_bit_encoding():
    levels = encode_halfbits(Frame.backward(0b10000000), stop_halfbits=0)
    assert levels[:2] == [0, 1]  # start bit is a logical 1: low then high
    assert levels[2:4] == [0, 1]  # first data bit 1
    assert levels[4:6] == [1, 0]  # then 0


def test_identical_answers_do_not_collide():
    assert resolve_collision([Frame.backward(0xFF)] * 5) == Frame.backward(0xFF)


def test_different_answers_give_framing_error():
    assert resolve_collision([Frame.backward(0x12), Frame.backward(0x34)]) is None


def test_bad_halfbits():
    with pytest.raises(ManchesterError):
        decode_halfbits([0, 0, 1, 1, 0, 1])


def test_pulses_roundtrip_with_tolerance():
    f = Frame.forward16(0xFE, 0x80)
    pulses = encode_pulses(f)
    jittered = [(lv, d * (1.08 if i % 2 else 0.93)) for i, (lv, d) in enumerate(pulses)]
    assert decode_pulses([(1, 5000)] + jittered + [(1, 3000)]) == f


def test_pulse_out_of_tolerance():
    with pytest.raises(ManchesterError):
        decode_pulses([(0, 250.0), (1, 416.7)])


def test_timing_constants():
    assert math.isclose(T.TE, 416.6667e-6, rel_tol=1e-4)
    assert math.isclose(T.FORWARD_16_DURATION * 1000, 15.83, abs_tol=0.01)
    assert math.isclose(T.BACKWARD_DURATION * 1000, 9.17, abs_tol=0.01)
    assert math.isclose(T.FORWARD_24_DURATION * 1000, 22.5, abs_tol=0.01)
    assert T.FORWARD_SETTLING[1].max < T.FORWARD_SETTLING[2].min


def test_gear_address_decoding():
    assert GearAddress.decode(0x01) == GearAddress(AddrKind.SHORT, 0, True)
    assert GearAddress.decode(0x7E) == GearAddress(AddrKind.SHORT, 63, False)
    assert GearAddress.decode(0x9F).kind == AddrKind.GROUP and GearAddress.decode(0x9F).value == 15
    assert GearAddress.decode(0xFF).kind == AddrKind.BROADCAST
    assert GearAddress.decode(0xFD).kind == AddrKind.BROADCAST_UNADDRESSED
    assert GearAddress.decode(0xA5).kind == AddrKind.SPECIAL
    assert parse_gear_target("A5") == 0x0B
    assert parse_gear_target("G2") == 0x85
    assert parse_gear_target("bc") == 0xFF


def test_device_address_and_instance_byte():
    assert DeviceAddress.decode(DeviceAddress.short(10)).value == 10
    assert DeviceAddress.decode(DeviceAddress.group(31)).value == 31
    assert DeviceAddress.decode(0xC1).kind == AddrKind.SPECIAL
    assert InstanceByte.decode(0xFF).kind == InstKind.DEVICE
    assert InstanceByte.decode(0x05) == InstanceByte(InstKind.NUMBER, 5)
    assert InstanceByte.decode(0xC3) == InstanceByte(InstKind.TYPE, 3)
    assert InstanceByte.decode(0x81) == InstanceByte(InstKind.GROUP, 1)


def test_dimming_curve():
    assert math.isclose(log_curve_percent(1), 0.1, rel_tol=1e-9)
    assert math.isclose(log_curve_percent(254), 100.0, rel_tol=1e-9)
    assert log_curve_percent(0) == 0
    # every step is the same ratio
    r1 = log_curve_percent(101) / log_curve_percent(100)
    r2 = log_curve_percent(201) / log_curve_percent(200)
    assert math.isclose(r1, r2)
    for lvl in (1, 50, 128, 200, 254):
        assert percent_to_log_level(log_curve_percent(lvl)) == lvl


def test_fade_tables():
    assert fade_time_seconds(0) == 0
    assert math.isclose(fade_time_seconds(1), 0.7071, abs_tol=1e-3)
    assert math.isclose(fade_time_seconds(15), 90.51, abs_tol=0.01)
    assert math.isclose(fade_rate_steps_per_second(1), 357.8, abs_tol=0.1)
    assert math.isclose(fade_rate_steps_per_second(15), 2.795, abs_tol=0.01)
    assert extended_fade_seconds(9, 1) == pytest.approx(1.0)
    assert extended_fade_seconds(15, 4) == pytest.approx(960.0)
    assert extended_fade_seconds(3, 0) == 0


def test_command_tables():
    assert GEAR_COMMANDS[0x90].name == "QUERY STATUS" and GEAR_COMMANDS[0x90].answer
    assert GEAR_COMMANDS[0x2E].twice
    assert GEAR_COMMANDS[0x17].name_for(0x17) == "GO TO SCENE 7"
    assert GEAR_SPECIAL[0xA5].twice and GEAR_SPECIAL[0xA9].answer
    assert DEVICE_COMMANDS[0x30].name == "QUERY DEVICE STATUS"
    assert find_by_name("QUERY ACTUAL LEVEL")[0][1] == 0xA0


def test_decoder_handles_device_type_context():
    d = FrameDecoder()
    assert d.decode(Frame.forward16(0xC1, 8)).text == "ENABLE DEVICE TYPE 8"
    assert "SET TEMPORARY COLOUR TEMPERATURE" in d.decode(Frame.forward16(0x01, 0xE7)).text
    assert "no device type" in d.decode(Frame.forward16(0x01, 0xE7)).text
    assert decode(Frame.forward16(0x00, 0xFF)) == "A0: DAPC MASK (stop fade)"


@pytest.mark.parametrize("ev", [
    Event(EventScheme.INSTANCE, 0x002, instance_type=1, instance_number=3),
    Event(EventScheme.DEVICE, 0x1FF, short_address=17, instance_type=4),
    Event(EventScheme.DEVICE_INSTANCE, 0x005, short_address=63, instance_number=31),
    Event(EventScheme.DEVICE_GROUP, 0x3FF, device_group=30, instance_type=3),
    Event(EventScheme.INSTANCE_GROUP, 0x001, instance_group=7, instance_type=1),
])
def test_event_roundtrip(ev):
    frame = ev.encode()
    assert frame.bits == 24 and frame.bit(16) == 0
    back = Event.decode(frame)
    assert back.scheme == ev.scheme and back.info == ev.info
    for attr in ("short_address", "instance_type", "instance_number", "device_group", "instance_group"):
        if getattr(ev, attr) is not None:
            assert getattr(back, attr) == getattr(ev, attr)


def test_command_frames_are_not_events():
    assert Event.decode(Frame.forward24(0x01, 0xFF, 0x30)) is None


def test_failing_timer_does_not_stop_the_bus():
    from dali_sim.bus import Bus
    from dali_sim.clock import ManualClock

    bus = Bus(ManualClock())
    hits = []
    bus.call_later(0.1, lambda t: 1 / 0)
    bus.call_later(0.2, lambda t: hits.append(t))
    bus.advance(1)
    assert hits == [0.2]
    assert "ZeroDivisionError" in bus.errors[0]
    assert any("internal error" in r.text for r in bus.history)
