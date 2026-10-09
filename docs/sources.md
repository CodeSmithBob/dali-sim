# Sources

dali-sim models DALI from public information. It contains **no text from IEC 62386**. Behaviour is described in our own words, and clause references point readers who own the standard to the right place.

## Standards (not included, for reference)

- IEC 62386-101 (system components), -102 (control gear), -103 (control devices), -201..-224 (device types for control gear), -250..-253 (D4i), -301..-305 (input devices). Published by the IEC; purchase from the IEC webstore or national standards bodies.
- DALI Alliance: DALI-2 and D4i certification programme descriptions and public technical guides, [dali-alliance.org](https://www.dali-alliance.org).

## Public material consulted

| Source | Used for |
|---|---|
| [python-dali](https://github.com/sde1000/python-dali) (LGPL-3.0) | byte layouts of the hasseb, Tridonic/Lunatone USB, Lunatone LUBA, AtxLED and daliserver interfaces; cross-check of command names. **No code was copied**: dali-sim's drivers are independent implementations. |
| [daliserver](https://github.com/onitake/daliserver) protocol (as implemented by python-dali's client) | daliserver TCP protocol |
| Lunatone DALI USB data sheets (via search results) | USB vendor/product ID 17B5:0020 and HID class; LUBA used over RS232 and USB virtual COM ports |
| General public DALI literature known to the authors | protocol model in `protocol/`, `gear/`, `devices/`: physical layer, timing, commands, variables, commissioning, events |
| CIE 1931 / sRGB formulas; black-body colour approximation (Tanner Helland) | colour display only (not part of DALI) |

The Lunatone "LUBA protocol" document (lunatone.com) is the primary source for LUBA. It was not reachable from the build environment, so its values come from python-dali's implementation, which cites it. They still need a check against the original.

## Recommended public reading for verification

- DALI Alliance public pages and technical guides: [dali-alliance.org](https://www.dali-alliance.org) (system overview, DALI-2 and D4i programmes, part list)
- Microchip application note AN1465, "Digitally Addressable Lighting Interface (DALI) Communication"
- Vendor application notes on DALI from NXP, ST and Texas Instruments
- Wikipedia, "Digital Addressable Lighting Interface"
- Interface manuals from hasseb, Lunatone, Tridonic and AtxLED

Contributors: when you check a value in the table below against one of these, add the document and page to the pull request.

## Compatibility checks done

- python-dali's AtxLED driver and its daliserver driver work unchanged against dali-sim's emulators (`--pty-protocol atxled`, `--daliserver-port`), including send-twice configuration.

## To verify

These values are implemented as described in common public material, but they still need a second independent public source or a check on real hardware. Corrections with a source are very welcome.

| Item | Where | Current assumption |
|---|---|---|
| Forward-frame settling time windows per priority 1..5 | `protocol/timing.py` | 13.5–14.7, 14.9–16.1, 16.3–17.7, 17.9–19.3, 19.5–21.1 ms |
| Backward-frame "no answer" timeout | `protocol/timing.py` | 12.4 ms after the forward frame |
| Extended fade time multipliers | `protocol/dimming.py` | 0 = off, 100 ms, 1 s, 10 s, 1 min |
| `QUERY VERSION NUMBER` for DALI-1 gear | `gear/control_gear.py` | 1.0 encoded as 0x04 |
| Memory bank 1 layout (D4i fields) | `memory.py` | plain storage with lock byte at 0x02 |
| DT6 bit layouts of gear type, features, failure status | `gear/led.py` | as in python-dali and AN1465 |
| DT8 `QUERY COLOUR VALUE` selector numbers for temporary (192+) and report (208+) values | `gear/colour.py` | see code |
| DT8 automatic activation bit and scene colour storage | `gear/colour.py` | bit 0 of gear features/status |
| DT1 bit layouts (mode, status, failure, features) and test timing selectors | `gear/emergency.py` | see code |
| 24-bit event frame bit layout per event scheme | `protocol/events.py` | see module docstring |
| 103 `INITIALISE` data: 0..63, 0x7F unaddressed, 0xFF all | `devices/control_device.py` | as stated |
| Push-button default event filter and default timers | `devices/instances.py` | filter: short, long start/repeat/stop, stuck/free; short 500 ms, double off, repeat 160 ms, stuck 20 s |
| Occupancy sensor event info bits and filter bits | `protocol/events.py`, `devices/instances.py` | bit 0 movement, bit 1 occupied, bit 2 repeat, bit 3 presence sensor |
| Instance-type command opcodes for 302/303/304 | `protocol/commands.py` | see tables |
| hasseb, Tridonic, Lunatone and AtxLED protocols | `adapters/` | as in the module docstrings; untested on hardware |
| AtxLED framing-error report | `adapters/atxled.py` | none known; framing errors may show as "no answer" |
