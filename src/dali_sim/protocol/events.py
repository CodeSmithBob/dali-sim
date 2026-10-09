"""Event messages sent by control devices (IEC 62386-103).

An event is a 24-bit forward frame with bit 16 = 0 (commands have bit 16 = 1).
Bits 23..10 say who sent it (the "event scheme"), bits 9..0 hold the 10-bit
event information whose meaning depends on the instance type.

Layout per scheme (bit 23 first):

==================  ====  =========  ====  ==  =========  ==========
scheme              b23   b22..b17   b16   b15 b14..b10   b9..b0
==================  ====  =========  ====  ==  =========  ==========
device (1)          0     short      0     0   inst.type  event info
device/instance (2) 0     short      0     1   inst.num   event info
device group (3)    1     0 + group  0     0   inst.type  event info
instance (0)        1     0 + type   0     1   inst.num   event info
instance group (4)  1     1 + group  0     0   inst.type  event info
==================  ====  =========  ====  ==  =========  ==========

For "device group" and "instance" the five bits below bit 22 hold the group
or instance type; bit 22 is 0.  For "instance group" bit 22 is 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .frames import Frame


class EventScheme(IntEnum):
    INSTANCE = 0
    DEVICE = 1
    DEVICE_INSTANCE = 2
    DEVICE_GROUP = 3
    INSTANCE_GROUP = 4


@dataclass(frozen=True)
class Event:
    scheme: EventScheme
    info: int  # 10-bit event information
    short_address: int | None = None
    device_group: int | None = None
    instance_type: int | None = None
    instance_number: int | None = None
    instance_group: int | None = None

    def encode(self) -> Frame:
        info = self.info & 0x3FF
        s = self.scheme
        if s == EventScheme.DEVICE:
            v = (self.short_address << 17) | (self.instance_type << 10)
        elif s == EventScheme.DEVICE_INSTANCE:
            v = (self.short_address << 17) | (1 << 15) | (self.instance_number << 10)
        elif s == EventScheme.DEVICE_GROUP:
            v = (1 << 23) | (self.device_group << 17) | (self.instance_type << 10)
        elif s == EventScheme.INSTANCE:
            v = (1 << 23) | (self.instance_type << 17) | (1 << 15) | (self.instance_number << 10)
        elif s == EventScheme.INSTANCE_GROUP:
            v = (1 << 23) | (1 << 22) | (self.instance_group << 17) | (self.instance_type << 10)
        else:  # pragma: no cover
            raise ValueError(s)
        return Frame(24, v | info)

    @classmethod
    def decode(cls, frame: Frame) -> Event | None:
        if frame.bits != 24 or frame.bit(16):
            return None
        v = frame.value
        info = v & 0x3FF
        b23, b22, b15 = (v >> 23) & 1, (v >> 22) & 1, (v >> 15) & 1
        f17 = (v >> 17) & 0x3F
        f17_5 = (v >> 17) & 0x1F
        f10 = (v >> 10) & 0x1F
        if not b23:
            if not b15:
                return cls(EventScheme.DEVICE, info, short_address=f17, instance_type=f10)
            return cls(EventScheme.DEVICE_INSTANCE, info, short_address=f17, instance_number=f10)
        if not b22:
            if not b15:
                return cls(EventScheme.DEVICE_GROUP, info, device_group=f17_5, instance_type=f10)
            return cls(EventScheme.INSTANCE, info, instance_type=f17_5, instance_number=f10)
        if not b15:
            return cls(EventScheme.INSTANCE_GROUP, info, instance_group=f17_5, instance_type=f10)
        return None  # reserved

    def source(self) -> str:
        s = self.scheme
        if s == EventScheme.DEVICE:
            return f"D{self.short_address} type {self.instance_type}"
        if s == EventScheme.DEVICE_INSTANCE:
            return f"D{self.short_address} I{self.instance_number}"
        if s == EventScheme.DEVICE_GROUP:
            return f"DG{self.device_group} type {self.instance_type}"
        if s == EventScheme.INSTANCE:
            return f"type {self.instance_type} I{self.instance_number}"
        return f"IG{self.instance_group} type {self.instance_type}"


# ---------------------------------------------------------------------------
# Event information per instance type
# ---------------------------------------------------------------------------

class PushButtonEvent(IntEnum):
    """IEC 62386-301 event codes (10-bit event info)."""

    RELEASED = 0x000
    PRESSED = 0x001
    SHORT_PRESS = 0x002
    DOUBLE_PRESS = 0x005
    LONG_PRESS_START = 0x009
    LONG_PRESS_REPEAT = 0x00A
    LONG_PRESS_STOP = 0x00B
    BUTTON_FREE = 0x00E
    BUTTON_STUCK = 0x00F


#: Event filter bit for each push button event (SET EVENT FILTER, DTR0).
PUSH_BUTTON_FILTER_BITS = {
    PushButtonEvent.RELEASED: 0,
    PushButtonEvent.PRESSED: 1,
    PushButtonEvent.SHORT_PRESS: 2,
    PushButtonEvent.DOUBLE_PRESS: 3,
    PushButtonEvent.LONG_PRESS_START: 4,
    PushButtonEvent.LONG_PRESS_REPEAT: 5,
    PushButtonEvent.LONG_PRESS_STOP: 6,
    PushButtonEvent.BUTTON_STUCK: 7,
    PushButtonEvent.BUTTON_FREE: 7,
}


class OccupancyEvent(IntEnum):
    """IEC 62386-303 event information.

    Bit 0: movement (1) / no movement (0).  Bit 1: occupied (1) / vacant (0).
    Bit 2: repeat ("still") report.  Bit 3: sensor type is "presence" (1) or
    "movement" (0) based.
    """

    NO_MOVEMENT = 0b0000
    MOVEMENT = 0b0001
    VACANT = 0b0000  # with bit 1 clear
    OCCUPIED = 0b0010
    REPEAT = 0b0100
    PRESENCE_SENSOR = 0b1000


def occupancy_info(occupied: bool, movement: bool, repeat: bool = False, presence: bool = False) -> int:
    return int(movement) | (int(occupied) << 1) | (int(repeat) << 2) | (int(presence) << 3)


def describe_event_info(instance_type: int | None, info: int) -> str:
    if instance_type == 1:
        try:
            return PushButtonEvent(info).name.replace("_", " ").lower()
        except ValueError:
            return f"push button event {info:#05x}"
    if instance_type == 3:
        parts = ["occupied" if info & 2 else "vacant", "movement" if info & 1 else "no movement"]
        if info & 4:
            parts.append("repeat")
        return ", ".join(parts)
    if instance_type in (2, 4):
        return f"value {info}"
    return f"info {info:#05x}"
