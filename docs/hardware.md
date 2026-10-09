# Hardware and bridges

## How the pieces connect

```
 real DALI bus ── interface (USB / serial / GPIO PHY) ── adapter ── bridge ── simulator
```

- An **adapter** speaks one interface's protocol (`dali-sim adapters` lists them).
- A **bridge** connects an adapter with the simulated bus in one of three modes.
- Without a bridge, the CLI commands `scan`, `commission`, `test`, `send` and `sniff` use an adapter directly with `--adapter SPEC`.

## Supported interfaces

| Spec | Interface | Link | 24-bit | Sniff | Answer as device | Status |
|---|---|---|---|---|---|---|
| `hasseb[:/dev/hidrawN]` | hasseb DALI Master | USB HID 04CC:0802 | no | no | no | from public drivers, untested here |
| `tridonic[:/dev/hidrawN]` | Tridonic DALI USB | USB HID 17B5:0020 | yes | yes | no | from public drivers, untested here |
| `lunatone:/dev/hidrawN` | older Lunatone DALI USB (same USB ID and protocol as Tridonic) | USB HID | yes | yes | no | as above |
| `lunatone:/dev/ttyX` | Lunatone LUBA interfaces (DALI RS232 LUBA; USB variants with a virtual COM port) | serial 38400 | yes | yes | no | from public sources, untested here |
| `atxled:/dev/serial0` | AtxLED DALI HAT (Raspberry Pi) | serial 19200 | yes | no | no | from public drivers; checked against python-dali's driver through the emulator |
| `serial:/dev/ttyX` | any firmware speaking the [dali-sim serial protocol](serial-protocol.md) | serial / USB CDC | per `V` | per `V` | per `V` | specified here |
| `gpio:tx=17,rx=27[,tx_invert=0,rx_invert=1]` | Raspberry Pi GPIO + DALI PHY, via pigpio | GPIO | yes | yes | yes (timing permitting) | experimental, tested with a pigpio fake |
| `daliserver:HOST[:PORT]` | a [daliserver](https://github.com/onitake/daliserver) instance | TCP 55825 | no | no | no | checked against python-dali's driver |
| `remote:HOST:PORT` | another dali-sim | TCP 5555 | yes | yes | no | tested |
| `loopback` | a second simulated bus | in-process | yes | yes | yes | tested |

"Untested here" means the drivers follow the byte layouts used by open-source drivers that work with these interfaces (see [sources.md](sources.md)), and they pass tests against byte-level emulators of the interfaces. Nobody has run them against the physical devices yet. **Please send a hardware report** (issue template) when you try one: that is how the table gets to "tested".

### Linux permissions

USB HID interfaces appear as `/dev/hidrawN`. Give your user access with a udev rule, for example `/etc/udev/rules.d/50-dali.rules`:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="04cc", ATTRS{idProduct}=="0802", MODE="0660", GROUP="plugdev", SYMLINK+="dali/hasseb"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="17b5", ATTRS{idProduct}=="0020", MODE="0660", GROUP="plugdev", SYMLINK+="dali/daliusb"
```

Without a path, dali-sim finds the device by its USB ID (via `/sys/class/hidraw` on Linux, or hidapi elsewhere).

### Raspberry Pi

- **AtxLED HAT:** enable the serial port (`raspi-config` → Interface → Serial, no login shell), then `--adapter atxled:/dev/serial0`.
- **DIY PHY on GPIO:** connect an opto-isolated receiver to `rx` and a bus driver transistor to `tx`. Run `sudo pigpiod`, then `--adapter gpio:tx=17,rx=27`. Most PHYs invert the receive signal (`rx_invert=1`, the default); set `tx_invert=1` if yours pulls the bus low on a high pin. A DALI bus also needs a **bus power supply** (current limited, 16 V typ.): the interface alone does not power the bus unless it says so.
- **DIY microcontroller interface:** use firmware that speaks the [serial protocol](serial-protocol.md) and `--adapter serial:/dev/ttyACM0`. It handles the timing in the microcontroller, which is more robust than GPIO bit-banging.

## Bridge modes

Start a bridge with `dali-sim serve CONFIG --adapter SPEC --bridge-mode MODE`. It shows on the **Hardware** tab, and the Console, Commissioning and Test bench tabs can then target it ("Bus: hardware: ...").

### `controller`: the simulator drives real devices

Everything you can do on the virtual bus works on the real one: console commands, scans, commissioning, the test bench. Frames and answers appear in the bus monitor marked `[hw]`. Works with every interface.

### `devices`: a real controller drives virtual devices

The adapter must **sniff** (report frames from other transmitters). Each forward frame seen on the real bus is delivered to the virtual units, so virtual lamps follow the real controller's commands and virtual sensors can be configured by it.

To **answer queries**, the interface must transmit a backward frame 5.5 to 10.5 ms after the forward frame. USB interfaces built for controllers usually cannot do this on request, and USB round trips alone take a few milliseconds. Options:

- `gpio` on a Raspberry Pi (pigpio waveforms; Python must react within the window, which usually works on an idle Pi);
- a microcontroller with the [serial protocol](serial-protocol.md) and the `back` capability (the microcontroller sends the stored answer in time);
- `loopback`, for software tests.

Without that capability, the bridge says so on the Hardware tab and counts the queries it could not answer.

### `hybrid`: one bus, half real, half virtual

Frames from the simulator's controllers (UI, rules, commissioning, test bench) go to both buses. Answers are merged as on a real wired-AND bus: a virtual and a real unit answering differently produce a framing error. Real events are fed into the virtual bus so virtual application controllers react to real buttons. Use it to extend a small real test setup to 64 units.

## Making the simulator look like an interface

The other direction: your software expects an interface, and you give it the simulator.

| Your software talks to | Run | Then point it at |
|---|---|---|
| a serial interface with the dali-sim protocol | `dali-sim serve --pty` | `/tmp/dali-sim-tty` |
| an AtxLED HAT | `dali-sim serve --pty /tmp/atx --pty-protocol atxled` | `/tmp/atx` |
| a Lunatone LUBA interface | `dali-sim serve --pty /tmp/luba --pty-protocol luba` | `/tmp/luba` |
| daliserver (e.g. python-dali `DaliServer`) | `dali-sim serve --daliserver-port 55825` | `localhost:55825` |
| anything, over TCP | `dali-sim serve` | the [TCP API](api.md), port 5555 |

USB HID emulation (appearing as a real hasseb or Tridonic stick through Linux `/dev/uhid`) is not implemented yet. The protocol emulators exist (`dali_sim/emulators.py`), so this is a good contribution.
