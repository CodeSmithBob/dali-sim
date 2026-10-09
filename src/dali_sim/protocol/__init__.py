"""DALI protocol primitives: frames, timing, addressing, command tables."""

from .address import AddrKind, DeviceAddress, GearAddress, InstanceByte, parse_gear_target
from .decode import FrameDecoder, decode
from .events import Event, EventScheme, PushButtonEvent
from .frames import Frame, FrameKind, resolve_collision

__all__ = [
    "AddrKind",
    "DeviceAddress",
    "Event",
    "EventScheme",
    "Frame",
    "FrameDecoder",
    "FrameKind",
    "GearAddress",
    "InstanceByte",
    "PushButtonEvent",
    "decode",
    "parse_gear_target",
    "resolve_collision",
]
