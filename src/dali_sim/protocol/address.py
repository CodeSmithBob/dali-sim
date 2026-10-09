"""Addressing for 16-bit (control gear) and 24-bit (control device) frames."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AddrKind(str, Enum):
    SHORT = "short"
    GROUP = "group"
    BROADCAST = "broadcast"
    BROADCAST_UNADDRESSED = "broadcast-unaddressed"
    SPECIAL = "special"  # special command, the address byte is the opcode
    RESERVED = "reserved"


@dataclass(frozen=True)
class GearAddress:
    """Decoded first byte of a 16-bit forward frame.

    Layout of the address byte (bit 7 .. bit 0):

    * ``0AAAAAAS`` short address 0..63
    * ``100GGGGS`` group 0..15
    * ``1111110S`` broadcast to gear without a short address (DALI-2)
    * ``1111111S`` broadcast
    * ``101xxxx1`` / ``110xxxx1`` special commands (0xA1..0xDF, odd)

    ``S`` (bit 0) is 0 for "direct arc power control" (the second byte is a
    level) and 1 for a command (the second byte is an opcode).
    """

    kind: AddrKind
    value: int | None
    is_command: bool  # selector bit S

    @classmethod
    def decode(cls, byte: int) -> GearAddress:
        s = bool(byte & 1)
        if byte < 0x80:
            return cls(AddrKind.SHORT, (byte >> 1) & 0x3F, s)
        if byte < 0xA0:
            return cls(AddrKind.GROUP, (byte >> 1) & 0x0F, s)
        if byte in (0xFE, 0xFF):
            return cls(AddrKind.BROADCAST, None, s)
        if byte in (0xFC, 0xFD):
            return cls(AddrKind.BROADCAST_UNADDRESSED, None, s)
        if 0xA0 <= byte <= 0xCB and s:
            return cls(AddrKind.SPECIAL, byte, True)
        return cls(AddrKind.RESERVED, byte, s)

    @staticmethod
    def short(n: int, command: bool = True) -> int:
        if not 0 <= n <= 63:
            raise ValueError("short address must be 0..63")
        return (n << 1) | int(command)

    @staticmethod
    def group(n: int, command: bool = True) -> int:
        if not 0 <= n <= 15:
            raise ValueError("gear group must be 0..15")
        return 0x80 | (n << 1) | int(command)

    @staticmethod
    def broadcast(command: bool = True) -> int:
        return 0xFE | int(command)

    @staticmethod
    def broadcast_unaddressed(command: bool = True) -> int:
        return 0xFC | int(command)

    def __str__(self) -> str:
        if self.kind == AddrKind.SHORT:
            return f"A{self.value}"
        if self.kind == AddrKind.GROUP:
            return f"G{self.value}"
        if self.kind == AddrKind.BROADCAST:
            return "BC"
        if self.kind == AddrKind.BROADCAST_UNADDRESSED:
            return "BC-unaddr"
        return f"0x{self.value:02X}"


def parse_gear_target(text: str) -> int:
    """Parse ``A5``, ``G3``, ``BC``, ``BCU`` or ``5`` into an address byte (S=1)."""
    t = text.strip().upper()
    if t in ("BC", "BROADCAST", "ALL"):
        return GearAddress.broadcast()
    if t in ("BCU", "BC-UNADDR", "UNADDRESSED"):
        return GearAddress.broadcast_unaddressed()
    if t.startswith("G"):
        return GearAddress.group(int(t[1:]))
    if t.startswith("A"):
        t = t[1:]
    return GearAddress.short(int(t))


# --------------------------------------------------------------------------
# 24-bit frames (IEC 62386-103)
# --------------------------------------------------------------------------

#: Address byte that marks a 24-bit special command.
DEVICE_SPECIAL_ADDRESS = 0xC1


@dataclass(frozen=True)
class DeviceAddress:
    """Decoded first byte of a 24-bit command frame (bit 0 = 1).

    * ``0AAAAAA1`` short address 0..63
    * ``10GGGGG1`` device group 0..31
    * ``11000001`` (0xC1) special command
    * ``11111101`` (0xFD) broadcast to devices without short address
    * ``11111111`` (0xFF) broadcast
    """

    kind: AddrKind
    value: int | None

    @classmethod
    def decode(cls, byte: int) -> DeviceAddress:
        if byte < 0x80:
            return cls(AddrKind.SHORT, (byte >> 1) & 0x3F)
        if byte < 0xC0:
            return cls(AddrKind.GROUP, (byte >> 1) & 0x1F)
        if byte == 0xFF:
            return cls(AddrKind.BROADCAST, None)
        if byte == 0xFD:
            return cls(AddrKind.BROADCAST_UNADDRESSED, None)
        if 0xC1 <= byte <= 0xDF:
            return cls(AddrKind.SPECIAL, byte)
        return cls(AddrKind.RESERVED, byte)

    @staticmethod
    def short(n: int) -> int:
        if not 0 <= n <= 63:
            raise ValueError("short address must be 0..63")
        return (n << 1) | 1

    @staticmethod
    def group(n: int) -> int:
        if not 0 <= n <= 31:
            raise ValueError("device group must be 0..31")
        return 0x80 | (n << 1) | 1

    BROADCAST = 0xFF
    BROADCAST_UNADDRESSED = 0xFD

    def __str__(self) -> str:
        if self.kind == AddrKind.SHORT:
            return f"D{self.value}"
        if self.kind == AddrKind.GROUP:
            return f"DG{self.value}"
        if self.kind == AddrKind.BROADCAST:
            return "BC"
        if self.kind == AddrKind.BROADCAST_UNADDRESSED:
            return "BC-unaddr"
        return f"0x{self.value:02X}"


class InstKind(str, Enum):
    NUMBER = "instance"
    GROUP = "instance-group"
    TYPE = "instance-type"
    FEATURE_NUMBER = "feature-instance"
    FEATURE_GROUP = "feature-instance-group"
    FEATURE_TYPE = "feature-instance-type"
    FEATURE_BROADCAST = "feature-broadcast"
    BROADCAST = "instance-broadcast"
    FEATURE_DEVICE = "feature-device"
    DEVICE = "device"
    RESERVED = "reserved"


@dataclass(frozen=True)
class InstanceByte:
    """Decoded second byte of a 24-bit command frame.

    * ``000NNNNN`` instance number 0..31
    * ``100GGGGG`` instance group 0..31
    * ``110TTTTT`` instance type 0..31
    * ``001NNNNN`` feature on instance number
    * ``101GGGGG`` feature on instance group
    * ``011TTTTT`` feature on instance type
    * ``11111100`` (0xFC) feature on all instances
    * ``11111101`` (0xFD) all instances (instance broadcast)
    * ``11111110`` (0xFE) feature on the device
    * ``11111111`` (0xFF) the device itself (device commands)
    """

    kind: InstKind
    value: int | None

    @classmethod
    def decode(cls, byte: int) -> InstanceByte:
        top = byte >> 5
        low = byte & 0x1F
        if byte == 0xFF:
            return cls(InstKind.DEVICE, None)
        if byte == 0xFE:
            return cls(InstKind.FEATURE_DEVICE, None)
        if byte == 0xFD:
            return cls(InstKind.BROADCAST, None)
        if byte == 0xFC:
            return cls(InstKind.FEATURE_BROADCAST, None)
        return cls(
            {
                0b000: InstKind.NUMBER,
                0b100: InstKind.GROUP,
                0b110: InstKind.TYPE,
                0b001: InstKind.FEATURE_NUMBER,
                0b101: InstKind.FEATURE_GROUP,
                0b011: InstKind.FEATURE_TYPE,
            }.get(top, InstKind.RESERVED),
            low,
        )

    @staticmethod
    def number(n: int) -> int:
        return n & 0x1F

    @staticmethod
    def group(n: int) -> int:
        return 0x80 | (n & 0x1F)

    @staticmethod
    def type(t: int) -> int:
        return 0xC0 | (t & 0x1F)

    DEVICE = 0xFF
    ALL_INSTANCES = 0xFD

    def __str__(self) -> str:
        if self.kind == InstKind.DEVICE:
            return "device"
        if self.kind == InstKind.BROADCAST:
            return "all-instances"
        if self.value is None:
            return self.kind.value
        prefix = {InstKind.NUMBER: "I", InstKind.GROUP: "IG", InstKind.TYPE: "IT"}.get(self.kind)
        return f"{prefix}{self.value}" if prefix else f"{self.kind.value}:{self.value}"
