# Limitations

dali-sim aims to behave like real DALI units as described in public sources. It does not cover everything yet. Known gaps:

## Physical layer

- The bus is simulated at frame level with half-bit superposition for collisions. Analogue effects (voltage drop on long cables, slow edges, bus power supply current limits, the 2 mA per unit budget) are not modelled.
- Transmitter collision detection is modelled as "both back off and retry". DALI-2's exact collision avoidance, detection and recovery (break timing, retry rules) is simplified.
- Answers start at the middle of the 5.5..10.5 ms window. Units with early or late answers are not modelled; a timing-fault option is planned.

## Control gear (102)

- One logical unit per gear (no multi-channel gear sharing bank 0).
- ENABLE DAPC SEQUENCE is accepted but has no effect.
- Memory bank 1 is plain lockable storage; the D4i field layout (part 251) is not interpreted. Banks 189+ (D4i energy and diagnostics) are not present.
- Operating modes other than 0 are not supported.
- RESET is immediate; real gear may take up to 300 ms.
- DALI-1 gear (`dali_version: 1`) only changes the version answers; it still accepts DALI-2 commands.

## Device types

- **DT6:** failures are injected from the UI; reference measurement and the current protector are not simulated. Fast fade time is stored but not used for fading.
- **DT8:** primary-N colour control is stored but not rendered; xy/Tc step commands do not fade; gamut and Tc limits are simplified; STORE XY-COORDINATE PRIMARY N and ASSIGN COLOUR TO LINKED CHANNEL are accepted without effect. The QUERY COLOUR VALUE selector numbering for temporary and report values needs checking (see sources.md).
- **DT1:** test scheduling (intervals, delays, timeouts) is stored but tests only start on command; battery charging is faster than real. Physical selection is not modelled.
- Not modelled yet: DT0 specifics (part 201), DT7 switching (208), DT9 sequencer (210), DT15..23, D4i DT49..52.

## Control devices (103 and 30x)

- Feature instances (bytes `001xxxxx` etc.) are decoded but no features are implemented.
- Power cycle notification events are not sent.
- Event filter bit layouts for occupancy sensors and the push-button default filter need checking (see sources.md).
- Part 305 (colour sensors) and later instance types are not modelled.

## Hardware adapters

- The Hasseb, Tridonic, Lunatone and AtxLED drivers were written from public protocol descriptions and open-source drivers. They have **not been tested on real hardware** by the authors yet. Please send hardware reports.
- Device emulation (`--bridge-mode devices`) can only answer queries with interfaces that transmit backward frames on request within the timing window. Most USB interfaces made for controllers cannot. See [hardware.md](hardware.md).

## Security

The web UI and APIs have no authentication. See [SECURITY.md](../SECURITY.md).
