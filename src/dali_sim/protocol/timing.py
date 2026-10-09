"""DALI physical-layer timing constants.

Values describe the DALI bus as specified by IEC 62386-101 (DALI-2).  They are
stated here in our own words from public sources (see docs/sources.md); where a
value has not yet been cross-checked against a second public source it is
listed in docs/sources.md under "To verify".

All times are in seconds unless the name says otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Nominal bit rate of the DALI bus.
BAUD_RATE = 1200

#: Nominal half-bit time ("Te").  One bit is two half bits (Manchester).
TE = 1.0 / (2 * BAUD_RATE)  # 416.67 us

#: Allowed half-bit tolerance for a transmitter (+-10 % in DALI-2).
TE_TOLERANCE = 0.10

#: Stop condition: the bus stays idle (high) for at least 4 Te after a frame.
STOP_CONDITION = 4 * TE

#: A bus held low for longer than this is a "system failure" (bus power down).
SYSTEM_FAILURE_TIMEOUT = 0.5  # >500 ms low

#: A low period of at least this long is interpreted as a "break" used for
#: collision recovery by multi-master transmitters.
BREAK_TIME_MIN = 1.2e-3


def frame_duration(bits: int) -> float:
    """Time a frame with ``bits`` data bits occupies on the bus.

    A frame is one start bit plus ``bits`` data bits, every bit being 2 Te,
    followed by the stop condition.
    """
    return (1 + bits) * 2 * TE + STOP_CONDITION


FORWARD_16_DURATION = frame_duration(16)  # ~15.8 ms
FORWARD_24_DURATION = frame_duration(24)  # ~22.5 ms
BACKWARD_DURATION = frame_duration(8)  # ~9.2 ms


@dataclass(frozen=True)
class Window:
    """A time window ``[min, max]`` in seconds."""

    min: float
    max: float

    def contains(self, value: float) -> bool:
        return self.min <= value <= self.max

    @property
    def mid(self) -> float:
        return (self.min + self.max) / 2


#: Time between the end of a forward frame and the start of the backward
#: frame sent by the receiver (DALI-2 receiver requirement).
FORWARD_TO_BACKWARD = Window(5.5e-3, 10.5e-3)

#: How long a transmitter waits for an answer before treating it as "no
#: answer".  The window above plus the first bit of a backward frame.
BACKWARD_FRAME_TIMEOUT = 12.4e-3

#: Minimum idle time after a backward frame before the next forward frame.
BACKWARD_TO_FORWARD_MIN = 2.4e-3

#: Settling time between two forward frames, by priority.  Multi-master
#: transmitters (DALI-2) wait the time of their priority after the bus became
#: idle; a lower number wins arbitration because it waits less.
#: Priority 1 is reserved for transactions (e.g. the second frame of a
#: "send twice" command or a frame directly following an answer).
FORWARD_SETTLING = {
    1: Window(13.5e-3, 14.7e-3),
    2: Window(14.9e-3, 16.1e-3),
    3: Window(16.3e-3, 17.7e-3),
    4: Window(17.9e-3, 19.3e-3),
    5: Window(19.5e-3, 21.1e-3),
}

#: Configuration commands must be received twice, the second time within this
#: period of the first, with no other command in between.
SEND_TWICE_TIMEOUT = 0.1

#: A special-mode ("initialisation") state lasts this long unless refreshed by
#: another INITIALISE command.
INITIALISE_TIMEOUT = 15 * 60

#: Window inside which a control gear must respond to the identify command
#: (it identifies itself for this long).
IDENTIFY_DURATION = 10.0

#: Duration of an UP / DOWN command fade (at the fade rate).
UP_DOWN_DURATION = 0.2

#: Duration of a QUIESCENT mode for control devices (IEC 62386-103).
QUIESCENT_MODE_DURATION = 15 * 60

#: Bus electrical levels (volts).
BUS_HIGH_MIN = 9.5
BUS_HIGH_MAX = 22.5
BUS_LOW_MIN = -6.5
BUS_LOW_MAX = 6.5
BUS_CURRENT_MAX = 0.250  # amperes, bus power supply limit
BUS_CURRENT_PER_UNIT_MAX = 0.002  # amperes, max quiescent draw per device

#: Address space limits.
MAX_CONTROL_GEAR = 64
MAX_CONTROL_DEVICES = 64
GEAR_GROUPS = 16
DEVICE_GROUPS = 32
SCENES = 16
