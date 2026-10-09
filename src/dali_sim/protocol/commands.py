"""Command tables for DALI control gear (IEC 62386-102) and control devices
(IEC 62386-103), including the device-type extensions the simulator models.

Names follow the command names used throughout public DALI literature.  The
tables hold facts only (opcode, name, whether an answer is expected, whether
the command must be sent twice); behaviour lives in the device models.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cmd:
    opcode: int
    name: str
    answer: bool = False
    twice: bool = False
    #: Which data transfer registers the command reads (informational).
    uses: tuple[str, ...] = ()
    #: Introduced with DALI-2 (edition 2 of the part).
    dali2: bool = False
    #: Removed in DALI-2 (still answered by legacy DALI-1 gear).
    legacy: bool = False
    #: For ranges like GO TO SCENE n: number of consecutive opcodes.
    span: int = 1
    tags: tuple[str, ...] = field(default=())

    def name_for(self, opcode: int) -> str:
        if self.span > 1:
            return f"{self.name} {opcode - self.opcode}"
        return self.name


def _table(cmds: list[Cmd]) -> dict[int, Cmd]:
    out: dict[int, Cmd] = {}
    for c in cmds:
        for op in range(c.opcode, c.opcode + c.span):
            if op in out:
                raise ValueError(f"duplicate opcode {op:#x}: {c.name} / {out[op].name}")
            out[op] = c
    return out


# ---------------------------------------------------------------------------
# IEC 62386-102: control gear, standard commands (address byte with S = 1)
# ---------------------------------------------------------------------------
GEAR_COMMANDS = _table([
    Cmd(0x00, "OFF"),
    Cmd(0x01, "UP"),
    Cmd(0x02, "DOWN"),
    Cmd(0x03, "STEP UP"),
    Cmd(0x04, "STEP DOWN"),
    Cmd(0x05, "RECALL MAX LEVEL"),
    Cmd(0x06, "RECALL MIN LEVEL"),
    Cmd(0x07, "STEP DOWN AND OFF"),
    Cmd(0x08, "ON AND STEP UP"),
    Cmd(0x09, "ENABLE DAPC SEQUENCE"),
    Cmd(0x0A, "GO TO LAST ACTIVE LEVEL", dali2=True),
    Cmd(0x0B, "CONTINUOUS UP", dali2=True),
    Cmd(0x0C, "CONTINUOUS DOWN", dali2=True),
    Cmd(0x10, "GO TO SCENE", span=16),
    Cmd(0x20, "RESET", twice=True),
    Cmd(0x21, "STORE ACTUAL LEVEL IN DTR0", twice=True),
    Cmd(0x22, "SAVE PERSISTENT VARIABLES", twice=True, dali2=True),
    Cmd(0x23, "SET OPERATING MODE", twice=True, uses=("DTR0",), dali2=True),
    Cmd(0x24, "RESET MEMORY BANK", twice=True, uses=("DTR0",), dali2=True),
    Cmd(0x25, "IDENTIFY DEVICE", twice=True, dali2=True),
    Cmd(0x2A, "SET MAX LEVEL", twice=True, uses=("DTR0",)),
    Cmd(0x2B, "SET MIN LEVEL", twice=True, uses=("DTR0",)),
    Cmd(0x2C, "SET SYSTEM FAILURE LEVEL", twice=True, uses=("DTR0",)),
    Cmd(0x2D, "SET POWER ON LEVEL", twice=True, uses=("DTR0",)),
    Cmd(0x2E, "SET FADE TIME", twice=True, uses=("DTR0",)),
    Cmd(0x2F, "SET FADE RATE", twice=True, uses=("DTR0",)),
    Cmd(0x30, "SET EXTENDED FADE TIME", twice=True, uses=("DTR0",), dali2=True),
    Cmd(0x40, "SET SCENE", twice=True, uses=("DTR0",), span=16),
    Cmd(0x50, "REMOVE FROM SCENE", twice=True, span=16),
    Cmd(0x60, "ADD TO GROUP", twice=True, span=16),
    Cmd(0x70, "REMOVE FROM GROUP", twice=True, span=16),
    Cmd(0x80, "SET SHORT ADDRESS", twice=True, uses=("DTR0",)),
    Cmd(0x81, "ENABLE WRITE MEMORY", twice=True),
    Cmd(0x90, "QUERY STATUS", answer=True),
    Cmd(0x91, "QUERY CONTROL GEAR PRESENT", answer=True),
    Cmd(0x92, "QUERY LAMP FAILURE", answer=True),
    Cmd(0x93, "QUERY LAMP POWER ON", answer=True),
    Cmd(0x94, "QUERY LIMIT ERROR", answer=True),
    Cmd(0x95, "QUERY RESET STATE", answer=True),
    Cmd(0x96, "QUERY MISSING SHORT ADDRESS", answer=True),
    Cmd(0x97, "QUERY VERSION NUMBER", answer=True),
    Cmd(0x98, "QUERY CONTENT DTR0", answer=True),
    Cmd(0x99, "QUERY DEVICE TYPE", answer=True),
    Cmd(0x9A, "QUERY PHYSICAL MINIMUM", answer=True),
    Cmd(0x9B, "QUERY POWER FAILURE", answer=True),
    Cmd(0x9C, "QUERY CONTENT DTR1", answer=True),
    Cmd(0x9D, "QUERY CONTENT DTR2", answer=True),
    Cmd(0x9E, "QUERY OPERATING MODE", answer=True, dali2=True),
    Cmd(0x9F, "QUERY LIGHT SOURCE TYPE", answer=True, dali2=True),
    Cmd(0xA0, "QUERY ACTUAL LEVEL", answer=True),
    Cmd(0xA1, "QUERY MAX LEVEL", answer=True),
    Cmd(0xA2, "QUERY MIN LEVEL", answer=True),
    Cmd(0xA3, "QUERY POWER ON LEVEL", answer=True),
    Cmd(0xA4, "QUERY SYSTEM FAILURE LEVEL", answer=True),
    Cmd(0xA5, "QUERY FADE TIME/FADE RATE", answer=True),
    Cmd(0xA6, "QUERY MANUFACTURER SPECIFIC MODE", answer=True, dali2=True),
    Cmd(0xA7, "QUERY NEXT DEVICE TYPE", answer=True, dali2=True),
    Cmd(0xA8, "QUERY EXTENDED FADE TIME", answer=True, dali2=True),
    Cmd(0xAA, "QUERY CONTROL GEAR FAILURE", answer=True, dali2=True),
    Cmd(0xB0, "QUERY SCENE LEVEL", answer=True, span=16),
    Cmd(0xC0, "QUERY GROUPS 0-7", answer=True),
    Cmd(0xC1, "QUERY GROUPS 8-15", answer=True),
    Cmd(0xC2, "QUERY RANDOM ADDRESS (H)", answer=True),
    Cmd(0xC3, "QUERY RANDOM ADDRESS (M)", answer=True),
    Cmd(0xC4, "QUERY RANDOM ADDRESS (L)", answer=True),
    Cmd(0xC5, "READ MEMORY LOCATION", answer=True, uses=("DTR0", "DTR1")),
    Cmd(0xFF, "QUERY EXTENDED VERSION NUMBER", answer=True, tags=("device-type",)),
])

#: Opcodes 0xE0..0xFF are "application extended commands": their meaning
#: depends on the device type selected by a preceding ENABLE DEVICE TYPE.
APPLICATION_EXTENDED_RANGE = range(0xE0, 0x100)


# ---------------------------------------------------------------------------
# IEC 62386-102: special commands (the address byte is the opcode)
# ---------------------------------------------------------------------------
GEAR_SPECIAL = {
    0xA1: Cmd(0xA1, "TERMINATE"),
    0xA3: Cmd(0xA3, "DTR0"),
    0xA5: Cmd(0xA5, "INITIALISE", twice=True),
    0xA7: Cmd(0xA7, "RANDOMISE", twice=True),
    0xA9: Cmd(0xA9, "COMPARE", answer=True),
    0xAB: Cmd(0xAB, "WITHDRAW"),
    0xAD: Cmd(0xAD, "PING", dali2=True),
    0xB1: Cmd(0xB1, "SEARCHADDRH"),
    0xB3: Cmd(0xB3, "SEARCHADDRM"),
    0xB5: Cmd(0xB5, "SEARCHADDRL"),
    0xB7: Cmd(0xB7, "PROGRAM SHORT ADDRESS"),
    0xB9: Cmd(0xB9, "VERIFY SHORT ADDRESS", answer=True),
    0xBB: Cmd(0xBB, "QUERY SHORT ADDRESS", answer=True),
    0xBD: Cmd(0xBD, "PHYSICAL SELECTION", legacy=True),
    0xC1: Cmd(0xC1, "ENABLE DEVICE TYPE"),
    0xC3: Cmd(0xC3, "DTR1"),
    0xC5: Cmd(0xC5, "DTR2"),
    0xC7: Cmd(0xC7, "WRITE MEMORY LOCATION", answer=True, uses=("DTR0", "DTR1")),
    0xC9: Cmd(0xC9, "WRITE MEMORY LOCATION - NO REPLY", uses=("DTR0", "DTR1"), dali2=True),
}


# ---------------------------------------------------------------------------
# Device type extensions (application extended commands, 0xE0..0xFF)
# ---------------------------------------------------------------------------

#: IEC 62386-207, device type 6: LED modules.
DT6_COMMANDS = _table([
    Cmd(0xE0, "REFERENCE SYSTEM POWER", twice=True, legacy=True),
    Cmd(0xE1, "ENABLE CURRENT PROTECTOR", twice=True, legacy=True),
    Cmd(0xE2, "DISABLE CURRENT PROTECTOR", twice=True, legacy=True),
    Cmd(0xE3, "SELECT DIMMING CURVE", twice=True, uses=("DTR0",)),
    Cmd(0xE4, "SET FAST FADE TIME", twice=True, uses=("DTR0",)),
    Cmd(0xED, "QUERY GEAR TYPE", answer=True),
    Cmd(0xEE, "QUERY DIMMING CURVE", answer=True),
    Cmd(0xEF, "QUERY POSSIBLE OPERATING MODES", answer=True),
    Cmd(0xF0, "QUERY FEATURES", answer=True),
    Cmd(0xF1, "QUERY FAILURE STATUS", answer=True),
    Cmd(0xF2, "QUERY SHORT CIRCUIT", answer=True),
    Cmd(0xF3, "QUERY OPEN CIRCUIT", answer=True),
    Cmd(0xF4, "QUERY LOAD DECREASE", answer=True),
    Cmd(0xF5, "QUERY LOAD INCREASE", answer=True),
    Cmd(0xF6, "QUERY CURRENT PROTECTOR ACTIVE", answer=True),
    Cmd(0xF7, "QUERY THERMAL SHUT DOWN", answer=True),
    Cmd(0xF8, "QUERY THERMAL OVERLOAD", answer=True),
    Cmd(0xF9, "QUERY REFERENCE RUNNING", answer=True),
    Cmd(0xFA, "QUERY REFERENCE MEASUREMENT FAILED", answer=True),
    Cmd(0xFB, "QUERY CURRENT PROTECTOR ENABLED", answer=True),
    Cmd(0xFC, "QUERY OPERATING MODE (DT6)", answer=True),
    Cmd(0xFD, "QUERY FAST FADE TIME", answer=True),
    Cmd(0xFE, "QUERY MIN FAST FADE TIME", answer=True),
    Cmd(0xFF, "QUERY EXTENDED VERSION NUMBER", answer=True),
])

#: IEC 62386-209, device type 8: colour control.
DT8_COMMANDS = _table([
    Cmd(0xE0, "SET TEMPORARY X-COORDINATE", uses=("DTR0", "DTR1")),
    Cmd(0xE1, "SET TEMPORARY Y-COORDINATE", uses=("DTR0", "DTR1")),
    Cmd(0xE2, "ACTIVATE"),
    Cmd(0xE3, "X-COORDINATE STEP UP"),
    Cmd(0xE4, "X-COORDINATE STEP DOWN"),
    Cmd(0xE5, "Y-COORDINATE STEP UP"),
    Cmd(0xE6, "Y-COORDINATE STEP DOWN"),
    Cmd(0xE7, "SET TEMPORARY COLOUR TEMPERATURE TC", uses=("DTR0", "DTR1")),
    Cmd(0xE8, "COLOUR TEMPERATURE TC STEP COOLER"),
    Cmd(0xE9, "COLOUR TEMPERATURE TC STEP WARMER"),
    Cmd(0xEA, "SET TEMPORARY PRIMARY N DIMLEVEL", uses=("DTR0", "DTR1", "DTR2")),
    Cmd(0xEB, "SET TEMPORARY RGB DIMLEVEL", uses=("DTR0", "DTR1", "DTR2")),
    Cmd(0xEC, "SET TEMPORARY WAF DIMLEVEL", uses=("DTR0", "DTR1", "DTR2")),
    Cmd(0xED, "SET TEMPORARY RGBWAF CONTROL", uses=("DTR0",)),
    Cmd(0xEE, "COPY REPORT TO TEMPORARY"),
    Cmd(0xF0, "STORE TY PRIMARY N", twice=True, uses=("DTR0", "DTR1", "DTR2")),
    Cmd(0xF1, "STORE XY-COORDINATE PRIMARY N", twice=True, uses=("DTR2",)),
    Cmd(0xF2, "STORE COLOUR TEMPERATURE TC LIMIT", twice=True, uses=("DTR0", "DTR1", "DTR2")),
    Cmd(0xF3, "STORE GEAR FEATURES/STATUS", twice=True, uses=("DTR0",)),
    Cmd(0xF5, "ASSIGN COLOUR TO LINKED CHANNEL", twice=True, uses=("DTR0",)),
    Cmd(0xF6, "START AUTO CALIBRATION", twice=True),
    Cmd(0xF7, "QUERY GEAR FEATURES/STATUS", answer=True),
    Cmd(0xF8, "QUERY COLOUR STATUS", answer=True),
    Cmd(0xF9, "QUERY COLOUR TYPE FEATURES", answer=True),
    Cmd(0xFA, "QUERY COLOUR VALUE", answer=True, uses=("DTR0",)),
    Cmd(0xFB, "QUERY RGBWAF CONTROL", answer=True),
    Cmd(0xFC, "QUERY ASSIGNED COLOUR", answer=True, uses=("DTR0",)),
    Cmd(0xFF, "QUERY EXTENDED VERSION NUMBER", answer=True),
])

#: IEC 62386-202, device type 1: self-contained emergency lighting.
DT1_COMMANDS = _table([
    Cmd(0xE0, "REST"),
    Cmd(0xE1, "INHIBIT"),
    Cmd(0xE2, "RE-LIGHT/RESET INHIBIT"),
    Cmd(0xE3, "START FUNCTION TEST"),
    Cmd(0xE4, "START DURATION TEST"),
    Cmd(0xE5, "STOP TEST"),
    Cmd(0xE6, "RESET FUNCTION TEST DONE FLAG"),
    Cmd(0xE7, "RESET DURATION TEST DONE FLAG"),
    Cmd(0xE8, "RESET LAMP TIME"),
    Cmd(0xE9, "STORE DTR AS EMERGENCY LEVEL", twice=True, uses=("DTR0",)),
    Cmd(0xEA, "STORE TEST DELAY TIME HIGH BYTE", twice=True, uses=("DTR0",)),
    Cmd(0xEB, "STORE TEST DELAY TIME LOW BYTE", twice=True, uses=("DTR0",)),
    Cmd(0xEC, "STORE FUNCTION TEST INTERVAL", twice=True, uses=("DTR0",)),
    Cmd(0xED, "STORE DURATION TEST INTERVAL", twice=True, uses=("DTR0",)),
    Cmd(0xEE, "STORE TEST EXECUTION TIMEOUT", twice=True, uses=("DTR0",)),
    Cmd(0xEF, "STORE PROLONG TIME", twice=True, uses=("DTR0",)),
    Cmd(0xF0, "START IDENTIFICATION"),
    Cmd(0xF1, "QUERY BATTERY CHARGE", answer=True),
    Cmd(0xF2, "QUERY TEST TIMING", answer=True, uses=("DTR0",)),
    Cmd(0xF3, "QUERY DURATION TEST RESULT", answer=True),
    Cmd(0xF4, "QUERY LAMP EMERGENCY TIME", answer=True),
    Cmd(0xF5, "QUERY LAMP TOTAL OPERATION TIME", answer=True),
    Cmd(0xF6, "QUERY EMERGENCY LEVEL", answer=True),
    Cmd(0xF7, "QUERY EMERGENCY MIN LEVEL", answer=True),
    Cmd(0xF8, "QUERY EMERGENCY MAX LEVEL", answer=True),
    Cmd(0xF9, "QUERY RATED DURATION", answer=True),
    Cmd(0xFA, "QUERY EMERGENCY MODE", answer=True),
    Cmd(0xFB, "QUERY FEATURES", answer=True),
    Cmd(0xFC, "QUERY FAILURE STATUS", answer=True),
    Cmd(0xFD, "QUERY EMERGENCY STATUS", answer=True),
    Cmd(0xFE, "PERFORM DTR SELECTED FUNCTION"),
    Cmd(0xFF, "QUERY EXTENDED VERSION NUMBER", answer=True),
])

DEVICE_TYPE_COMMANDS: dict[int, dict[int, Cmd]] = {
    1: DT1_COMMANDS,
    6: DT6_COMMANDS,
    8: DT8_COMMANDS,
}

DEVICE_TYPE_NAMES = {
    0: "Fluorescent lamps (IEC 62386-201)",
    1: "Self-contained emergency lighting (IEC 62386-202)",
    2: "Discharge lamps (IEC 62386-203)",
    3: "Low-voltage halogen lamps (IEC 62386-204)",
    4: "Supply voltage controller for incandescent lamps (IEC 62386-205)",
    5: "Conversion from digital into DC voltage (IEC 62386-206)",
    6: "LED modules (IEC 62386-207)",
    7: "Switching function (IEC 62386-208)",
    8: "Colour control (IEC 62386-209)",
    9: "Sequencer (IEC 62386-210)",
    15: "Load referencing (IEC 62386-216)",
    16: "Thermal gear protection (IEC 62386-217)",
    17: "Dimming curve selection (IEC 62386-218)",
    19: "Centrally supplied emergency operation (IEC 62386-220)",
    20: "Load shedding (IEC 62386-221)",
    21: "Thermal lamp protection (IEC 62386-222)",
    23: "Non-replaceable light sources (IEC 62386-224)",
    49: "Integrated power supply (D4i, IEC 62386-250)",
    50: "Memory bank 1 extension (D4i, IEC 62386-251)",
    51: "Energy reporting (D4i, IEC 62386-252)",
    52: "Diagnostics and maintenance (D4i, IEC 62386-253)",
}


# ---------------------------------------------------------------------------
# IEC 62386-103: control devices (24-bit frames)
# ---------------------------------------------------------------------------

#: Commands addressed to the device (instance byte 0xFF).
DEVICE_COMMANDS = _table([
    Cmd(0x00, "IDENTIFY DEVICE", twice=True),
    Cmd(0x01, "RESET POWER CYCLE SEEN", twice=True),
    Cmd(0x10, "RESET", twice=True),
    Cmd(0x11, "RESET MEMORY BANK", twice=True, uses=("DTR0",)),
    Cmd(0x14, "SET SHORT ADDRESS", twice=True, uses=("DTR0",)),
    Cmd(0x15, "ENABLE WRITE MEMORY", twice=True),
    Cmd(0x16, "ENABLE APPLICATION CONTROLLER", twice=True),
    Cmd(0x17, "DISABLE APPLICATION CONTROLLER", twice=True),
    Cmd(0x18, "SET OPERATING MODE", twice=True, uses=("DTR0",)),
    Cmd(0x19, "ADD TO DEVICE GROUPS 0-15", twice=True, uses=("DTR1", "DTR2")),
    Cmd(0x1A, "ADD TO DEVICE GROUPS 16-31", twice=True, uses=("DTR1", "DTR2")),
    Cmd(0x1B, "REMOVE FROM DEVICE GROUPS 0-15", twice=True, uses=("DTR1", "DTR2")),
    Cmd(0x1C, "REMOVE FROM DEVICE GROUPS 16-31", twice=True, uses=("DTR1", "DTR2")),
    Cmd(0x1D, "START QUIESCENT MODE", twice=True),
    Cmd(0x1E, "STOP QUIESCENT MODE", twice=True),
    Cmd(0x1F, "ENABLE POWER CYCLE NOTIFICATION", twice=True),
    Cmd(0x20, "DISABLE POWER CYCLE NOTIFICATION", twice=True),
    Cmd(0x21, "SAVE PERSISTENT VARIABLES", twice=True),
    Cmd(0x30, "QUERY DEVICE STATUS", answer=True),
    Cmd(0x31, "QUERY APPLICATION CONTROLLER ERROR", answer=True),
    Cmd(0x32, "QUERY INPUT DEVICE ERROR", answer=True),
    Cmd(0x33, "QUERY MISSING SHORT ADDRESS", answer=True),
    Cmd(0x34, "QUERY VERSION NUMBER", answer=True),
    Cmd(0x35, "QUERY NUMBER OF INSTANCES", answer=True),
    Cmd(0x36, "QUERY CONTENT DTR0", answer=True),
    Cmd(0x37, "QUERY CONTENT DTR1", answer=True),
    Cmd(0x38, "QUERY CONTENT DTR2", answer=True),
    Cmd(0x39, "QUERY RANDOM ADDRESS (H)", answer=True),
    Cmd(0x3A, "QUERY RANDOM ADDRESS (M)", answer=True),
    Cmd(0x3B, "QUERY RANDOM ADDRESS (L)", answer=True),
    Cmd(0x3C, "READ MEMORY LOCATION", answer=True, uses=("DTR0", "DTR1")),
    Cmd(0x3D, "QUERY APPLICATION CONTROLLER ENABLED", answer=True),
    Cmd(0x3E, "QUERY OPERATING MODE", answer=True),
    Cmd(0x3F, "QUERY MANUFACTURER SPECIFIC MODE", answer=True),
    Cmd(0x40, "QUERY QUIESCENT MODE", answer=True),
    Cmd(0x41, "QUERY DEVICE GROUPS 0-7", answer=True),
    Cmd(0x42, "QUERY DEVICE GROUPS 8-15", answer=True),
    Cmd(0x43, "QUERY DEVICE GROUPS 16-23", answer=True),
    Cmd(0x44, "QUERY DEVICE GROUPS 24-31", answer=True),
    Cmd(0x45, "QUERY POWER CYCLE NOTIFICATION", answer=True),
    Cmd(0x46, "QUERY DEVICE CAPABILITIES", answer=True),
    Cmd(0x47, "QUERY EXTENDED VERSION NUMBER", answer=True, uses=("DTR0",)),
    Cmd(0x48, "QUERY RESET STATE", answer=True),
    Cmd(0x49, "QUERY APPLICATION CONTROLLER ALWAYS ACTIVE", answer=True),
])

#: Commands addressed to instances (instance byte selects instance(s)).
INSTANCE_COMMANDS = _table([
    Cmd(0x61, "SET EVENT PRIORITY", twice=True, uses=("DTR0",)),
    Cmd(0x62, "ENABLE INSTANCE", twice=True),
    Cmd(0x63, "DISABLE INSTANCE", twice=True),
    Cmd(0x64, "SET PRIMARY INSTANCE GROUP", twice=True, uses=("DTR0",)),
    Cmd(0x65, "SET INSTANCE GROUP 1", twice=True, uses=("DTR0",)),
    Cmd(0x66, "SET INSTANCE GROUP 2", twice=True, uses=("DTR0",)),
    Cmd(0x67, "SET EVENT SCHEME", twice=True, uses=("DTR0",)),
    Cmd(0x68, "SET EVENT FILTER", twice=True, uses=("DTR0", "DTR1", "DTR2")),
    Cmd(0x80, "QUERY INSTANCE TYPE", answer=True),
    Cmd(0x81, "QUERY RESOLUTION", answer=True),
    Cmd(0x82, "QUERY INSTANCE ERROR", answer=True),
    Cmd(0x83, "QUERY INSTANCE STATUS", answer=True),
    Cmd(0x84, "QUERY EVENT PRIORITY", answer=True),
    Cmd(0x86, "QUERY INSTANCE ENABLED", answer=True),
    Cmd(0x88, "QUERY PRIMARY INSTANCE GROUP", answer=True),
    Cmd(0x89, "QUERY INSTANCE GROUP 1", answer=True),
    Cmd(0x8A, "QUERY INSTANCE GROUP 2", answer=True),
    Cmd(0x8B, "QUERY EVENT SCHEME", answer=True),
    Cmd(0x8C, "QUERY INPUT VALUE", answer=True),
    Cmd(0x8D, "QUERY INPUT VALUE LATCH", answer=True),
    Cmd(0x8E, "QUERY FEATURE TYPE", answer=True),
    Cmd(0x8F, "QUERY NEXT FEATURE TYPE", answer=True),
    Cmd(0x90, "QUERY EVENT FILTER 0-7", answer=True),
    Cmd(0x91, "QUERY EVENT FILTER 8-15", answer=True),
    Cmd(0x92, "QUERY EVENT FILTER 16-23", answer=True),
])

#: Instance-type specific commands (opcodes 0x00..0x1F on an instance).
#: IEC 62386-301 push buttons.
PUSH_BUTTON_COMMANDS = _table([
    Cmd(0x00, "SET SHORT TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x01, "SET DOUBLE TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x02, "SET REPEAT TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x03, "SET STUCK TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x0A, "QUERY SHORT TIMER", answer=True),
    Cmd(0x0B, "QUERY SHORT TIMER MIN", answer=True),
    Cmd(0x0C, "QUERY DOUBLE TIMER", answer=True),
    Cmd(0x0D, "QUERY DOUBLE TIMER MIN", answer=True),
    Cmd(0x0E, "QUERY REPEAT TIMER", answer=True),
    Cmd(0x0F, "QUERY STUCK TIMER", answer=True),
])

#: IEC 62386-302 absolute input devices.
ABSOLUTE_INPUT_COMMANDS = _table([
    Cmd(0x00, "SET REPORT TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x01, "SET DEADTIME TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x0C, "QUERY DEADTIME TIMER", answer=True),
    Cmd(0x0E, "QUERY REPORT TIMER", answer=True),
    Cmd(0x0F, "QUERY SWITCH", answer=True),
])

#: IEC 62386-303 occupancy sensors.
OCCUPANCY_COMMANDS = _table([
    Cmd(0x00, "CATCH MOVEMENT"),
    Cmd(0x01, "SET HOLD TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x02, "SET REPORT TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x03, "SET DEADTIME TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x04, "CANCEL HOLD TIMER"),
    Cmd(0x0C, "QUERY DEADTIME TIMER", answer=True),
    Cmd(0x0D, "QUERY HOLD TIMER", answer=True),
    Cmd(0x0E, "QUERY REPORT TIMER", answer=True),
    Cmd(0x0F, "QUERY CATCHING", answer=True),
])

#: IEC 62386-304 light sensors.
LIGHT_SENSOR_COMMANDS = _table([
    Cmd(0x00, "SET REPORT TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x01, "SET HYSTERESIS", twice=True, uses=("DTR0",)),
    Cmd(0x02, "SET DEADTIME TIMER", twice=True, uses=("DTR0",)),
    Cmd(0x03, "SET HYSTERESIS MIN", twice=True, uses=("DTR0",)),
    Cmd(0x0C, "QUERY HYSTERESIS MIN", answer=True),
    Cmd(0x0D, "QUERY DEADTIME TIMER", answer=True),
    Cmd(0x0E, "QUERY REPORT TIMER", answer=True),
    Cmd(0x0F, "QUERY HYSTERESIS", answer=True),
])

INSTANCE_TYPE_COMMANDS: dict[int, dict[int, Cmd]] = {
    1: PUSH_BUTTON_COMMANDS,
    2: ABSOLUTE_INPUT_COMMANDS,
    3: OCCUPANCY_COMMANDS,
    4: LIGHT_SENSOR_COMMANDS,
}

INSTANCE_TYPE_NAMES = {
    0: "Generic (IEC 62386-103)",
    1: "Push buttons (IEC 62386-301)",
    2: "Absolute input devices (IEC 62386-302)",
    3: "Occupancy sensors (IEC 62386-303)",
    4: "Light sensors (IEC 62386-304)",
    5: "Colour sensors (IEC 62386-305)",
}

#: Special commands in 24-bit frames: address byte 0xC1, opcode in the
#: instance byte, data in the third byte.
DEVICE_SPECIAL = {
    0x00: Cmd(0x00, "TERMINATE"),
    0x01: Cmd(0x01, "INITIALISE", twice=True),
    0x02: Cmd(0x02, "RANDOMISE", twice=True),
    0x03: Cmd(0x03, "COMPARE", answer=True),
    0x04: Cmd(0x04, "WITHDRAW"),
    0x05: Cmd(0x05, "SEARCHADDRH"),
    0x06: Cmd(0x06, "SEARCHADDRM"),
    0x07: Cmd(0x07, "SEARCHADDRL"),
    0x08: Cmd(0x08, "PROGRAM SHORT ADDRESS"),
    0x09: Cmd(0x09, "VERIFY SHORT ADDRESS", answer=True),
    0x0A: Cmd(0x0A, "QUERY SHORT ADDRESS", answer=True),
    0x20: Cmd(0x20, "WRITE MEMORY LOCATION", answer=True, uses=("DTR0", "DTR1")),
    0x21: Cmd(0x21, "WRITE MEMORY LOCATION - NO REPLY", uses=("DTR0", "DTR1")),
    0x30: Cmd(0x30, "DTR0"),
    0x31: Cmd(0x31, "DTR1"),
    0x32: Cmd(0x32, "DTR2"),
    0x33: Cmd(0x33, "SEND TESTFRAME", uses=("DTR0", "DTR1", "DTR2")),
}

#: Special commands with a different address byte (two data bytes).
DEVICE_SPECIAL_2BYTE = {
    0xC5: Cmd(0xC5, "DIRECT WRITE MEMORY", answer=True),
    0xC7: Cmd(0xC7, "DTR1:DTR0"),
    0xC9: Cmd(0xC9, "DTR2:DTR1"),
}


def find_by_name(name: str) -> list[tuple[str, int, Cmd]]:
    """Look up commands by (case-insensitive) name across all tables."""
    want = name.strip().upper()
    tables = {
        "gear": GEAR_COMMANDS,
        "gear-special": GEAR_SPECIAL,
        "dt1": DT1_COMMANDS,
        "dt6": DT6_COMMANDS,
        "dt8": DT8_COMMANDS,
        "device": DEVICE_COMMANDS,
        "instance": INSTANCE_COMMANDS,
        "device-special": DEVICE_SPECIAL,
    }
    out = []
    for tname, table in tables.items():
        for op, cmd in table.items():
            if cmd.name_for(op) == want or (cmd.span == 1 and cmd.name == want):
                out.append((tname, op, cmd))
    return out
