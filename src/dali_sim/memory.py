"""Memory banks of control gear and control devices.

Bank 0 is read-only identification data.  Banks 1 and up are optional; byte
0x02 of such a bank is its lock byte: writing 0x55 there unlocks the other
locations for writing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LOCK_BYTE = 0x02
UNLOCK_VALUE = 0x55


@dataclass
class MemoryBank:
    number: int
    size: int  # number of implemented locations (last accessible = size - 1)
    data: bytearray = field(default_factory=bytearray)
    writable: set[int] = field(default_factory=set)
    #: Locations restored by RESET MEMORY BANK.
    reset_values: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.data) < self.size:
            self.data.extend(b"\xff" * (self.size - len(self.data)))
        self.data[0] = self.size - 1

    @property
    def last_location(self) -> int:
        return self.size - 1

    def read(self, loc: int) -> int | None:
        if loc > self.last_location:
            return None
        return self.data[loc]

    def locked(self) -> bool:
        return self.number != 0 and self.size > LOCK_BYTE and self.data[LOCK_BYTE] != UNLOCK_VALUE

    def write(self, loc: int, value: int) -> bool:
        """Write if allowed.  Returns True if the location took the value."""
        if loc > self.last_location or loc not in self.writable:
            return False
        if loc != LOCK_BYTE and self.locked():
            return False
        self.data[loc] = value & 0xFF
        return True

    def reset(self) -> None:
        if self.locked() and self.number != 0:
            return
        for loc, v in self.reset_values.items():
            self.data[loc] = v
        if self.size > LOCK_BYTE and self.number != 0:
            self.data[LOCK_BYTE] = 0xFF


def make_bank0(*, gtin: int, firmware: tuple[int, int], serial: int, hardware: tuple[int, int],
               v101: int, v102: int, v103: int, n_devices: int, n_gear: int, index: int,
               last_bank: int) -> MemoryBank:
    """Bank 0 in the DALI-2 layout (0x00..0x1A)."""
    b = MemoryBank(0, 0x1B)
    b.data[0x01] = 0xFF  # manufacturer specific indicator
    b.data[0x02] = last_bank
    b.data[0x03:0x09] = gtin.to_bytes(6, "big")
    b.data[0x09] = firmware[0]
    b.data[0x0A] = firmware[1]
    b.data[0x0B:0x13] = serial.to_bytes(8, "big")
    b.data[0x13] = hardware[0]
    b.data[0x14] = hardware[1]
    b.data[0x15] = v101
    b.data[0x16] = v102
    b.data[0x17] = v103
    b.data[0x18] = n_devices
    b.data[0x19] = n_gear
    b.data[0x1A] = index
    return b


def make_bank1() -> MemoryBank:
    """Bank 1: luminaire (OEM) information, all writable once unlocked.

    The field layout used by D4i (IEC 62386-251) is only partly modelled; the
    bank behaves as plain unlockable storage.
    """
    b = MemoryBank(1, 0x78)
    b.data[0x01] = 0xFF
    b.writable = set(range(0x02, 0x78))
    b.reset_values = {loc: 0xFF for loc in range(0x03, 0x78)}
    return b


def version_byte(major: int, minor: int) -> int:
    """Encode a version number the DALI way: major in bits 7..2, minor in 1..0."""
    return ((major & 0x3F) << 2) | (minor & 0x03)


DALI2_VERSION = version_byte(2, 0)  # 0x08
