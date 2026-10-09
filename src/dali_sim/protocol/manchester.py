"""Manchester decoding from edge timings, for bit-banged interfaces (GPIO,
logic analysers, microcontroller captures).

Input is a list of ``(level, duration_us)`` pulses as measured on the *bus*
(level 1 = idle/high, 0 = active/low).  Each pulse must be about one or two
half-bit times long (Te = 416.67 us); the receiver accepts the DALI-2 timing
tolerance window.
"""

from __future__ import annotations

from .frames import Frame, ManchesterError, decode_halfbits, encode_halfbits
from .timing import TE

TE_US = TE * 1e6

# Receiver acceptance windows (microseconds).  A receiver accepts half bits
# of 333..500 us and double half bits of 667..1000 us.
HALF_MIN, HALF_MAX = 333.0, 500.0
FULL_MIN, FULL_MAX = 667.0, 1000.0


def pulses_to_halfbits(pulses: list[tuple[int, float]]) -> list[int]:
    out: list[int] = []
    for level, dur in pulses:
        if HALF_MIN <= dur <= HALF_MAX:
            out.append(level)
        elif FULL_MIN <= dur <= FULL_MAX:
            out += [level, level]
        elif level == 1 and dur > FULL_MAX:
            out += [1, 1, 1, 1]  # stop condition / idle
            break
        else:
            raise ManchesterError(f"pulse of {dur:.0f} us at level {level} is out of tolerance")
    return out


def decode_pulses(pulses: list[tuple[int, float]]) -> Frame:
    """Decode a frame from measured pulses (starting with the first low)."""
    # Drop leading idle.
    i = 0
    while i < len(pulses) and pulses[i][0] == 1:
        i += 1
    return decode_halfbits(pulses_to_halfbits(pulses[i:]))


def encode_pulses(frame: Frame, te_us: float = TE_US) -> list[tuple[int, float]]:
    """Pulses to transmit ``frame`` (merged runs of equal level)."""
    levels = encode_halfbits(frame, stop_halfbits=0)
    pulses: list[tuple[int, float]] = []
    for lv in levels:
        if pulses and pulses[-1][0] == lv:
            pulses[-1] = (lv, pulses[-1][1] + te_us)
        else:
            pulses.append((lv, te_us))
    return pulses


__all__ = ["ManchesterError", "decode_pulses", "encode_pulses", "pulses_to_halfbits"]
