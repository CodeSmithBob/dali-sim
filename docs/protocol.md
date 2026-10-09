# DALI in short

This page explains DALI as the simulator models it, in our own words. It is a map, not a replacement for IEC 62386. Clause numbers are given so that readers who have the standard can look things up. Values that still need a second public source are listed in [sources.md](sources.md).

## The parts of IEC 62386

| Part | Topic | In dali-sim |
|---|---|---|
| 101 | System: physical layer, bus power, timing, multi-master | `protocol/timing.py`, `protocol/frames.py`, `bus.py` |
| 102 | Control gear: commands, variables, addressing | `gear/control_gear.py` |
| 103 | Control devices: 24-bit commands, instances, events | `devices/control_device.py` |
| 104 | Wireless and wired alternative transports | not modelled |
| 105 | Firmware transfer | not modelled |
| 150 | Auxiliary power supply | not modelled |
| 201..224 | Device types 0..23 for control gear (DT6 is part 207, DT8 is part 209, DT1 is part 202) | DT1, DT6, DT8 |
| 250..253 | D4i: integrated bus power, luminaire data, energy and diagnostics (DT49..52) | memory bank 1 only |
| 301..304 | Input devices: push buttons, absolute input, occupancy, light | all four |

## Physical layer (part 101)

- Two wires, polarity-free at the devices. The bus power supply delivers at most 250 mA. Each unit draws at most 2 mA when idle.
- **Idle is high** (9.5 to 22.5 V). A transmitter **pulls the bus low** (−6.5 to 6.5 V). Several transmitters together form a *wired-AND*: one low is enough to make the bus low.
- **1200 bit/s, Manchester (bi-phase) code.** One bit = two half bits of Te = 416.7 µs. A logical 1 is low then high (a rising edge in the middle of the bit), a logical 0 is high then low.
- Every frame starts with a **start bit** (a logical 1) and ends with at least 4 Te of idle (the **stop condition**). Bits are sent most significant first.

| Frame | Data bits | Duration incl. stop | Used for |
|---|---|---|---|
| Forward, 16-bit | 16 | 15.8 ms | control gear commands |
| Forward, 24-bit | 24 | 22.5 ms | control device commands and event messages |
| Backward | 8 | 9.2 ms | answers |

### Timing between frames

- An answer (backward frame) starts **5.5 to 10.5 ms** after the end of the forward frame that asked for it. If nothing has started after about 12.4 ms, there is no answer.
- After a backward frame the bus stays idle for at least 2.4 ms.
- Before a forward frame a transmitter waits a **settling time** that depends on its **priority** (1 to 5, about 13.5 ms up to 21 ms). Priority 1 is for the second frame of a transaction (e.g. the repeat of a send-twice command). Multi-master transmitters (DALI-2) win arbitration by waiting less.
- When two transmitters start together, each one checks the bus while it sends. A transmitter that sees a level it did not send stops; a *collision* has happened. Both try again later. The simulator records collisions and retries with a random back-off.
- A bus that stays low for more than about 500 ms means **bus power failure**. Control gear then goes to its *system failure level*.

### Answers and collisions

A backward frame is 8 bits. Many queries are "YES/NO" questions: YES is 0xFF, NO is *no answer*. When several units answer at once (a broadcast query), their frames add up on the wired-AND bus. Identical answers look like one answer. Different answers break the Manchester code, and the receiver sees a **framing error**. For a YES/NO question that still means "at least one said YES". The simulator superimposes the half bits exactly as the bus would (`protocol/frames.py: superimpose`).

## Control gear (part 102)

### The 16-bit forward frame

```
 address byte        opcode / level
 YAAAAAAS            DDDDDDDD
```

| Address byte | Meaning |
|---|---|
| `0AAAAAAS` | short address 0..63 |
| `100GGGGS` | group 0..15 |
| `1111110S` | all gear without a short address (DALI-2) |
| `1111111S` | broadcast |
| `101xxxx1`, `110xxxx1` | special command (the whole byte is the opcode; the second byte is data) |

The **selector bit S** decides what the second byte is. With S = 0 it is an arc power level: *direct arc power control* (DAPC), 0..254, and 255 means "stop fading". With S = 1 it is a command opcode.

### Commands

`dali-sim commands` prints the complete tables the simulator knows. The groups are:

- **Arc power** (0x00..0x1F): OFF, UP/DOWN (fade at the fade rate for 200 ms), STEP UP/DOWN, RECALL MAX/MIN, GO TO SCENE 0..15, and in DALI-2 GO TO LAST ACTIVE LEVEL and CONTINUOUS UP/DOWN.
- **Configuration** (0x20..0x81): RESET, SET MAX/MIN/POWER ON/SYSTEM FAILURE LEVEL, SET FADE TIME/RATE, SET SCENE, ADD TO/REMOVE FROM GROUP, SET SHORT ADDRESS and others. Most take their value from **DTR0** (data transfer register 0, set with the special command DTR0). They only execute if they are **received twice within 100 ms with no other frame between**. That protects settings against corrupted frames.
- **Queries** (0x90..0xC5): status, levels, settings, groups, scenes, random address, memory.
- **Application extended commands** (0xE0..0xFF): their meaning depends on the device type. They are only accepted directly after **ENABLE DEVICE TYPE n**.

### Variables

Each gear keeps the variables the commands work on. The table shows the ones the simulator models; the reset value is what RESET restores.

| Variable | Reset value | Notes |
|---|---|---|
| actualLevel | 254 | light output as an arc power level |
| powerOnLevel | 254 | level after mains power-up; 255 = last level |
| systemFailureLevel | 254 | level on bus power failure; 255 = no change |
| minLevel / maxLevel | physicalMinLevel / 254 | limits of arc power |
| fadeRate / fadeTime | 7 / 0 | see below |
| extendedFadeTime | 0 | DALI-2, used when fadeTime is 0 |
| scene 0..15 | 255 (none) | |
| gearGroups | 0 | 16-bit mask |
| shortAddress | no change | 255 = none |
| randomAddress, searchAddress | 0xFFFFFF | 24-bit |
| operatingMode | 0 | 0x80..0xFF manufacturer specific |

The **status byte** (QUERY STATUS) has, from bit 0 to bit 7: control gear failure, lamp failure, lamp on, limit error, fade running, reset state, no short address, power cycle seen.

### Dimming curve and fading

- The standard curve is **logarithmic**. Level 1 is 0.1 % and level 254 is 100 %; each step multiplies the light by the same factor (about 2.8 %). Level 0 is off.
- **Fade time** n (1..15) is 0.5 · √(2ⁿ) seconds, from 0.7 s up to 90.5 s, whatever the distance. A fade towards 0 dims to the minimum level and then switches off. A fade from off starts at the minimum level.
- **Fade rate** n (1..15) is 506 / √(2ⁿ) steps per second. It is used by UP and DOWN (for 200 ms).
- **Extended fade time** (DALI-2) = (base + 1) × multiplier, with multipliers 100 ms, 1 s, 10 s and 1 min.

### Memory banks

Memory is read with DTR1 = bank and DTR0 = location, then READ MEMORY LOCATION; DTR0 counts up after every read. Bank 0 is read-only and holds identification data: the last accessible location, GTIN, firmware version, serial number, hardware version and the versions of parts 101/102/103. In banks 1 and up, location 0x02 is a lock byte. Writing 0x55 there unlocks the bank; ENABLE WRITE MEMORY (sent twice) must come first.

### Commissioning: random addressing

New gear has no short address. A controller finds the units one by one:

1. **INITIALISE** (sent twice): all gear, or only gear without an address, enter the initialisation state for 15 minutes.
2. **RANDOMISE** (twice): every unit picks a random 24-bit address.
3. **Binary search**: the controller sets the 24-bit *search address* with SEARCHADDRH/M/L and sends **COMPARE**. Every unit whose random address is ≤ the search address answers YES. Halving the range 24 times isolates the lowest random address.
4. **PROGRAM SHORT ADDRESS** gives that unit an address. VERIFY SHORT ADDRESS checks it. **WITHDRAW** takes it out of the search.
5. Repeat until COMPARE gets no answer, then **TERMINATE**.

A unit needs about 25 COMPARE transactions, so a full bus of 64 gear takes a few minutes. The simulator shows the shrinking search range live on the Commissioning tab. If two units happen to pick the same random address, they get the same short address. The test bench detects this (they answer different values to QUERY RANDOM ADDRESS, which gives a framing error).

## Control devices (part 103)

Control devices are the *inputs* of a DALI system (buttons, sensors) and the *application controllers*. They use **24-bit frames**:

```
 address byte    instance byte    opcode
 0AAAAAA1        IIIIIIII         OOOOOOOO
```

- The address byte addresses short address 0..63, device groups 0..31 (`10GGGGG1`) or broadcast (0xFF, and 0xFD for units without an address). **0xC1** marks a *special command*: the second byte is the opcode, the third is data (DTR0/1/2, INITIALISE, COMPARE...).
- The **instance byte** selects the device itself (0xFF) or instances by number (`000NNNNN`), by instance group (`100GGGGG`) or by type (`110TTTTT`), or all instances (0xFD).
- A device has up to 32 **instances**, one per input. Instance types: 1 push button (part 301), 2 absolute input (302), 3 occupancy sensor (303), 4 light sensor (304).

### Event messages

Inputs report changes as **events**: 24-bit forward frames with **bit 16 = 0** (commands always have bit 16 = 1). Bits 9..0 carry the 10-bit event information; the other bits identify the sender according to the instance's **event scheme**:

| Scheme | Identifies the sender by |
|---|---|
| 0 instance | instance type + instance number |
| 1 device | short address + instance type |
| 2 device/instance | short address + instance number |
| 3 device group | device group + instance type |
| 4 instance group | instance group + instance type |

A device without a short address falls back to scheme 0. Every instance also has an **event filter** (which events it sends) and an **event priority** (2..5, the settling time it uses). In **quiescent mode** a device sends no events (for example during commissioning).

Push button events include pressed, released, short press, double press, long press start/repeat/stop, and stuck/free. Occupancy events carry occupied/vacant, movement/no movement and a repeat flag. Light sensors and absolute inputs report their value.

## Device types the simulator models

- **DT6, LED modules (207):** dimming curve selection (logarithmic or linear), gear type, possible operating modes, features and failure status (short circuit, open circuit, thermal...).
- **DT8, colour control (209):** colour temperature in mirek (1 000 000 / kelvin), CIE xy coordinates and up to six RGBWAF channels. Values are set as *temporary* values (from DTR0/DTR1/DTR2) and applied with ACTIVATE, or with the next arc power command when automatic activation is on. QUERY COLOUR VALUE returns the most significant byte as the answer and puts the least significant byte in DTR0.
- **DT1, self-contained emergency (202):** emergency level, battery charge, function and duration tests, rest and inhibit modes, test timing, status and failure bytes.

## Further reading

See [sources.md](sources.md) for the public documents used and the values that still need checking.
