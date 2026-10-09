"""Dimming curve and fade timing (IEC 62386-102, DT6 linear curve from -207)."""

from __future__ import annotations

import math

MASK = 0xFF  # "no change" / "not set" value of 8-bit variables


def log_curve_percent(level: int) -> float:
    """Light output in percent for arc power ``level`` on the standard
    logarithmic curve: level 1 is 0.1 %, level 254 is 100 %, and every step
    is the same ratio (about 2.8 %)."""
    if level <= 0:
        return 0.0
    level = min(level, 254)
    return 10 ** ((level - 1) / (253 / 3) - 1)


def linear_curve_percent(level: int) -> float:
    """Light output in percent on the linear curve (DT6 dimming curve 1)."""
    if level <= 0:
        return 0.0
    return min(level, 254) / 254 * 100


def percent_to_log_level(percent: float) -> int:
    """Closest arc power level for ``percent`` on the logarithmic curve."""
    if percent <= 0:
        return 0
    percent = min(percent, 100.0)
    level = round(1 + (253 / 3) * (math.log10(percent) + 1))
    return max(1, min(254, level))


def fade_time_seconds(fade_time: int) -> float:
    """Fade time in seconds for the 4-bit ``fade time`` variable (0..15).

    0 means "no fade" (or the extended fade time when that is set); other
    values follow 0.5 * sqrt(2 ** n) seconds, from 0.7 s to 90.5 s.
    """
    if not 0 <= fade_time <= 15:
        raise ValueError("fade time must be 0..15")
    if fade_time == 0:
        return 0.0
    return 0.5 * math.sqrt(2 ** fade_time)


def fade_rate_steps_per_second(fade_rate: int) -> float:
    """Fade rate in steps per second for the 4-bit ``fade rate`` variable
    (1..15), following 506 / sqrt(2 ** n): from 357.8 down to 2.8 steps/s."""
    if not 1 <= fade_rate <= 15:
        raise ValueError("fade rate must be 1..15")
    return 506 / math.sqrt(2 ** fade_rate)


#: Multipliers of the DALI-2 extended fade time (seconds).  Index 0 means the
#: extended fade time is 0 (no fade).
EXTENDED_FADE_MULTIPLIERS = (0.0, 0.1, 1.0, 10.0, 60.0)


def extended_fade_seconds(base: int, multiplier: int) -> float:
    """Extended fade time: (base + 1) * multiplier, base 0..15, multiplier
    0..4.  Values beyond the table count as 0."""
    if multiplier >= len(EXTENDED_FADE_MULTIPLIERS) or multiplier == 0:
        return 0.0
    return (base + 1) * EXTENDED_FADE_MULTIPLIERS[multiplier]


def decode_extended_fade(dtr0: int) -> tuple[int, int] | None:
    """Split the DTR0 value of SET EXTENDED FADE TIME into (base,
    multiplier); ``None`` if the multiplier is out of range."""
    base = dtr0 & 0x0F
    mult = (dtr0 >> 4) & 0x07
    if mult > 4 or dtr0 & 0x80:
        return None
    return base, mult


# Tables for documentation and the UI.
FADE_TIMES = {n: fade_time_seconds(n) for n in range(16)}
FADE_RATES = {n: fade_rate_steps_per_second(n) for n in range(1, 16)}


def mirek_to_kelvin(mirek: int) -> float:
    return 1_000_000 / mirek if mirek else 0.0


def kelvin_to_mirek(kelvin: float) -> int:
    return int(round(1_000_000 / kelvin)) if kelvin else 0
