"""DALI frames and Manchester (bi-phase) coding.

DALI sends bits most significant first.  Every bit lasts 2 Te.  A logical 1 is
low in the first half and high in the second half (rising edge in the middle);
a logical 0 is the opposite.  Every frame starts with a start bit (a 1) and
ends with an idle (high) stop condition.

The bus is "wired-AND": as soon as one transmitter pulls the line low, the bus
is low.  :func:`superimpose` uses that to model what a receiver sees when
several transmitters send at the same time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FrameKind(str, Enum):
    FORWARD_16 = "forward16"  # control gear commands (IEC 62386-102)
    FORWARD_24 = "forward24"  # control device commands and events (-103)
    BACKWARD = "backward"  # 8-bit answer


_KIND_BITS = {FrameKind.FORWARD_16: 16, FrameKind.FORWARD_24: 24, FrameKind.BACKWARD: 8}
_BITS_KIND = {v: k for k, v in _KIND_BITS.items()}


@dataclass(frozen=True)
class Frame:
    """A frame on the bus: ``bits`` data bits holding ``value``.

    Besides the three standard sizes the class can hold other sizes (DALI
    allows 20-bit and 32-bit "reserved" frames); the simulator passes those on
    without interpreting them.
    """

    bits: int
    value: int

    def __post_init__(self) -> None:
        if self.bits <= 0 or self.bits > 32:
            raise ValueError(f"unsupported frame size {self.bits}")
        if not 0 <= self.value < (1 << self.bits):
            raise ValueError(f"value {self.value:#x} does not fit in {self.bits} bits")

    # -- constructors -------------------------------------------------------
    @classmethod
    def forward16(cls, address: int, opcode: int) -> Frame:
        return cls(16, ((address & 0xFF) << 8) | (opcode & 0xFF))

    @classmethod
    def forward24(cls, address: int, instance: int, opcode: int) -> Frame:
        return cls(24, ((address & 0xFF) << 16) | ((instance & 0xFF) << 8) | (opcode & 0xFF))

    @classmethod
    def backward(cls, value: int) -> Frame:
        return cls(8, value & 0xFF)

    @classmethod
    def from_bytes(cls, data: bytes) -> Frame:
        return cls(len(data) * 8, int.from_bytes(data, "big"))

    # -- accessors ----------------------------------------------------------
    @property
    def kind(self) -> FrameKind | None:
        return _BITS_KIND.get(self.bits)

    @property
    def is_forward(self) -> bool:
        return self.bits in (16, 24)

    def byte(self, index: int) -> int:
        """Byte ``index`` counted from the most significant byte (0)."""
        nbytes = (self.bits + 7) // 8
        return (self.value >> (8 * (nbytes - 1 - index))) & 0xFF

    def to_bytes(self) -> bytes:
        return self.value.to_bytes((self.bits + 7) // 8, "big")

    def bit(self, n: int) -> int:
        """Bit ``n`` with bit 0 the least significant (as in IEC numbering)."""
        return (self.value >> n) & 1

    def hex(self) -> str:
        return self.to_bytes().hex(" ").upper()

    def __str__(self) -> str:
        return f"<{self.bits}-bit {self.hex()}>"


# --------------------------------------------------------------------------
# Manchester coding at half-bit (Te) resolution.
# Levels: 1 = high (idle), 0 = low (active).
# --------------------------------------------------------------------------

def encode_halfbits(frame: Frame, stop_halfbits: int = 4) -> list[int]:
    """Encode ``frame`` as a list of half-bit bus levels, start bit included."""
    out: list[int] = [0, 1]  # start bit (logical 1)
    for n in range(frame.bits - 1, -1, -1):
        if (frame.value >> n) & 1:
            out += [0, 1]
        else:
            out += [1, 0]
    out += [1] * stop_halfbits
    return out


class ManchesterError(ValueError):
    """The half-bit sequence is not a valid DALI frame (framing error)."""


def decode_halfbits(levels: list[int]) -> Frame:
    """Decode a half-bit level sequence produced by :func:`encode_halfbits`.

    Trailing idle (high) half bits are ignored.  Raises :class:`ManchesterError`
    if the sequence is not valid bi-phase code.
    """
    seq = list(levels)
    while seq and seq[-1] == 1:
        seq.pop()
    # A frame always ends with a data bit; a trailing logical 1 ends low-high
    # which we may have stripped partly.  Restore pairs.
    if len(seq) % 2:
        seq.append(1)
    if len(seq) < 4:
        raise ManchesterError("frame too short")
    if seq[0:2] != [0, 1]:
        raise ManchesterError("invalid start bit")
    value = 0
    bits = 0
    for i in range(2, len(seq), 2):
        pair = (seq[i], seq[i + 1])
        if pair == (0, 1):
            value = (value << 1) | 1
        elif pair == (1, 0):
            value <<= 1
        else:
            raise ManchesterError(f"invalid bit at half-bit {i}")
        bits += 1
    if bits not in (8, 16, 20, 24, 32):
        # A trailing 0-bit followed by idle looks one half-bit short when the
        # stop bits were stripped; accept the frame sizes DALI defines only.
        raise ManchesterError(f"unexpected frame length {bits}")
    return Frame(bits, value)


def superimpose(*frames: Frame, offsets: list[int] | None = None) -> list[int]:
    """Half-bit levels on a wired-AND bus where ``frames`` are sent together.

    ``offsets`` (in half bits) lets transmitters start slightly apart.
    """
    if not frames:
        return []
    offsets = offsets or [0] * len(frames)
    encoded = [[1] * off + encode_halfbits(f) for f, off in zip(frames, offsets)]
    length = max(len(e) for e in encoded)
    bus = [1] * length
    for e in encoded:
        for i, level in enumerate(e):
            bus[i] &= level
    return bus


def resolve_collision(frames: list[Frame]) -> Frame | None:
    """What a receiver decodes when all ``frames`` are transmitted at once.

    Returns the decoded frame, or ``None`` for a framing error.  Identical
    frames add up to the same frame; different ones normally break the
    Manchester code and give a framing error.
    """
    if not frames:
        return None
    if len(frames) == 1 or all(f == frames[0] for f in frames):
        return frames[0]
    try:
        return decode_halfbits(superimpose(*frames))
    except ManchesterError:
        return None
