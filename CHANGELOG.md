# Changelog

## Unreleased

- Several DALI lines in one simulator (`lines:` in the configuration): one web page with a line selector, one container, per-line TCP and daliserver ports, virtual serial ports and fake USB sticks with distinct serial numbers, `--adapter SPEC@N` and `--line N`. New example `three-lines.yaml` (3 × 64 lights).
- docs/connections.md: diagrams of every way to connect a DALI master, with status marks (tested, not yet tried, experimental, not built) and a help-wanted list.
- The gpio adapter spec accepts a pigpiod host (`gpio:tx=17,rx=27,host=...`), for Docker.
- Fake USB DALI sticks on Linux (`--usb-stick tridonic|lunatone|hasseb`, via `/dev/uhid`): DALI master software that expects one of these sticks talks to the simulator without wires.

## 0.1.0 (unreleased)

First public version.

- Bus engine: Manchester timing, priorities and settling times, multi-master collisions, wired-AND answers, fault injection, bus power failure.
- Control gear (IEC 62386-102) with DT1 emergency, DT6 LED and DT8 colour (Tc, xy, RGBWAF).
- Control devices (IEC 62386-103) with push buttons, absolute input, occupancy and light sensors.
- Application controller with YAML rules and a daylight loop; world model with rooms, daylight and people.
- Commissioning (binary search), scan, pre-compliance test bench with JUnit output.
- Web UI: floor plan, address map, device controls, bus monitor with waveforms, console, commissioning and test bench views.
- TCP JSON-lines API, REST/WebSocket API, daliserver-compatible server, virtual serial port emulating the dali-sim serial protocol, an AtxLED HAT or a Lunatone LUBA interface.
- Adapters: loopback, remote dali-sim, daliserver, dali-sim serial line protocol, Raspberry Pi GPIO (experimental), hasseb, Tridonic / Lunatone DALI USB (HID), Lunatone LUBA, AtxLED HAT. The vendor drivers follow public protocol descriptions and pass tests against byte-level emulators; they are untested on hardware.
- Docker image and compose file.
