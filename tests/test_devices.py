from conftest import f24
from dali_sim.automation import AutomationController
from dali_sim.devices import ControlDevice, LightSensor, OccupancySensor, PushButton
from dali_sim.gear import ControlGear
from dali_sim.protocol.events import Event, EventScheme, PushButtonEvent


def events(bus):
    out = []
    for r in bus.history:
        if r.kind == "event":
            out.append(Event.decode(r.frame))
    return out


def test_push_button_short_press(bus):
    d = bus.attach(ControlDevice("sw", short_address=5, instances=[PushButton()]))
    d.instances[0].event_scheme = 2
    d.instances[0].click(0.1)
    bus.advance(1)
    evs = events(bus)
    assert [e.info for e in evs] == [PushButtonEvent.SHORT_PRESS]
    assert evs[0].scheme == EventScheme.DEVICE_INSTANCE and evs[0].short_address == 5


def test_push_button_long_press(bus):
    d = bus.attach(ControlDevice("sw", short_address=5, instances=[PushButton()]))
    b = d.instances[0]
    b.press()
    bus.advance(1.0)
    b.release()
    bus.advance(0.5)
    infos = [e.info for e in events(bus)]
    assert infos[0] == PushButtonEvent.LONG_PRESS_START
    assert PushButtonEvent.LONG_PRESS_REPEAT in infos
    assert infos[-1] == PushButtonEvent.LONG_PRESS_STOP


def test_push_button_double_press(bus):
    d = bus.attach(ControlDevice("sw", instances=[PushButton(double_timer=20)]))
    d.instances[0].event_filter |= 1 << 3
    b = d.instances[0]
    b.click(0.05)
    bus.advance(0.15)
    b.click(0.05)
    bus.advance(1)
    assert [e.info for e in events(bus)] == [PushButtonEvent.DOUBLE_PRESS]


def test_unaddressed_device_uses_instance_scheme(bus):
    d = bus.attach(ControlDevice("sw", instances=[PushButton()]))
    d.instances[0].event_scheme = 2
    d.instances[0].click()
    bus.advance(1)
    assert events(bus)[0].scheme == EventScheme.INSTANCE


def test_quiescent_and_disabled_instances_are_silent(bus):
    d = bus.attach(ControlDevice("sw", short_address=1, instances=[PushButton(), PushButton()]))
    bus.request([f24(0x03, 0xFF, 0x1D)] * 2)  # START QUIESCENT MODE
    d.instances[0].click()
    bus.advance(1)
    assert events(bus) == []
    bus.request([f24(0x03, 0xFF, 0x1E)] * 2)
    bus.request([f24(0x03, 0x01, 0x63)] * 2)  # DISABLE INSTANCE 1
    d.instances[1].click()
    d.instances[0].click()
    bus.advance(1)
    assert len(events(bus)) == 1


def test_device_queries(bus):
    bus.attach(ControlDevice("multi", short_address=7, instances=[OccupancySensor(), LightSensor()]))
    assert bus.request(f24(0x0F, 0xFF, 0x35)).value == 2
    assert bus.request(f24(0x0F, 0x00, 0x80)).value == 3
    assert bus.request(f24(0x0F, 0x01, 0x80)).value == 4
    assert bus.request(f24(0x0F, 0xC4, 0x80)).value == 4  # by instance type
    assert bus.request(f24(0x0F, 0xFF, 0x34)).value == 0x08
    # DTR via 24-bit special commands
    bus.request([f24(0xC1, 0x30, 0x12), f24(0xC1, 0x31, 0x34)])
    assert bus.request(f24(0x0F, 0xFF, 0x36)).value == 0x12
    assert bus.request(f24(0x0F, 0xFF, 0x37)).value == 0x34


def test_light_sensor_input_value_latch(bus):
    d = bus.attach(ControlDevice("lux", short_address=2, instances=[LightSensor(resolution=10, max_lux=1023)]))
    d.instances[0].set_lux(1000)
    msb = bus.request(f24(0x05, 0x00, 0x8C)).value
    lsb = bus.request(f24(0x05, 0x00, 0x8D)).value
    assert ((msb << 8) | lsb) >> 6 == 1000


def test_occupancy_events(bus):
    d = bus.attach(ControlDevice("pir", short_address=3, instances=[OccupancySensor(hold_timer=3, report_timer=0)]))
    s = d.instances[0]
    s.motion(True)
    bus.advance(1)
    s.motion(False)
    bus.advance(31)
    infos = [e.info for e in events(bus)]
    assert infos[0] & 0b10  # occupied
    assert not infos[-1] & 0b10  # vacant after hold time


def test_event_collisions_are_resolved(bus):
    a = bus.attach(ControlDevice("a", short_address=1, instances=[PushButton()]))
    b = bus.attach(ControlDevice("b", short_address=2, instances=[PushButton()]))
    a.instances[0].event_scheme = b.instances[0].event_scheme = 2
    a.instances[0].press()
    b.instances[0].press()
    a.instances[0].release()
    b.instances[0].release()
    bus.advance(2)
    assert bus.stats["collisions"] >= 1
    senders = {e.short_address for e in events(bus)}
    assert senders == {1, 2}


def test_set_event_filter_and_groups(bus):
    d = bus.attach(ControlDevice("sw", short_address=4, instances=[PushButton()]))
    bus.request(f24(0xC1, 0x30, 0x02))  # DTR0: short press only
    bus.request([f24(0x09, 0x00, 0x68)] * 2)
    assert bus.request(f24(0x09, 0x00, 0x90)).value == 0x02
    bus.request([f24(0xC1, 0x31, 0x01), f24(0xC1, 0x32, 0x00)])
    bus.request([f24(0x09, 0xFF, 0x19)] * 2)  # ADD TO DEVICE GROUPS 0-15 (group 0)
    assert d.device_groups == 1
    assert bus.request(f24(0x81, 0xFF, 0x30)).answered  # device group 0 addressed


def test_automation_rules_toggle_group(bus):
    g = bus.attach(ControlGear("lamp", short_address=0))
    g._set_level(0, 0)
    g.groups = 1
    sw = bus.attach(ControlDevice("sw", short_address=0, instances=[PushButton()]))
    sw.instances[0].event_scheme = 2
    bus.attach(AutomationController(rules=[{"when": {"short_address": 0, "instance": 0, "event": "short_press"},
                                            "do": {"toggle": "G0"}}]))
    sw.instances[0].click()
    bus.advance(1)
    assert g.level_at(bus.now()) == 254
    sw.instances[0].click()
    bus.advance(1)
    assert g.level_at(bus.now()) == 0


def test_yaml_off_key_works(bus):
    import yaml

    g = bus.attach(ControlGear("lamp", short_address=0))
    pir = bus.attach(ControlDevice("pir", short_address=1, instances=[OccupancySensor(hold_timer=1, report_timer=0)]))
    pir.instances[0].event_scheme = 2
    rules = yaml.safe_load("- {when: {short_address: 1, event: vacant}, do: {off: A0}}")
    bus.attach(AutomationController(rules=rules))
    pir.instances[0].motion(True)
    bus.advance(1)
    pir.instances[0].motion(False)
    bus.advance(12)
    assert g.level_at(bus.now()) == 0
