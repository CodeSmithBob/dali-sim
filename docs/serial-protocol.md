# dali-sim serial line protocol (version 1)

A small text protocol between a host and a DALI interface built on a microcontroller (RP2040, ESP32, STM32, AVR...) or any other serial device. dali-sim speaks it in both roles:

- as **host**, through the `serial:/dev/ttyX` adapter, to use such an interface on a real bus;
- as **interface**, on a virtual serial port (`dali-sim serve --pty`), so software written for the interface can be tested against the simulator.

The protocol is defined here so that anyone can write firmware for it. The microcontroller does the time-critical work (Manchester coding, settling times, collision detection, the 5.5..10.5 ms answer window). The host only sends and receives lines.

## Framing

- ASCII lines ending in `\n` (`\r\n` accepted). Hex digits in either case.
- Suggested serial settings: 115200 baud, 8N1. USB CDC devices ignore the baud rate.
- Every host command gets exactly one response line, in order. Lines the interface sends on its own (monitor lines) start with `F`, `R`, `!` or `#` and can appear at any time between responses.

## Host to interface

| Command | Meaning | Response |
|---|---|---|
| `V` | version and capabilities | `V 1 <caps> <name>` with caps a comma list from `f24`, `sniff`, `back`, `ts` (`-` if none) |
| `S<hex>` | send one forward frame: 4 hex digits = 16 bit, 6 = 24 bit | answer line (below) |
| `T<hex>` | send the frame twice as one transaction (priority 1 for the repeat) | answer line for the second frame |
| `Q<hex>` | send and do **not** wait for an answer (events, commands without answer) | `K` |
| `P<n>` | priority 1..5 for following `S`/`T`/`Q` (default 3) | `K` |
| `M<n>` | monitor off (`M0`) or on (`M1`): report frames from other transmitters | `K` |
| `B<hh>` | transmit a backward frame now (device emulation). The interface must start it 5.5..10.5 ms after the last forward frame it received. | `K`, or `L` if that window has passed |

### Answer lines

| Line | Meaning |
|---|---|
| `A<hh>` | backward frame received, value `hh` |
| `N` | no answer within the timeout |
| `E` | something was received but it was not a valid backward frame (several units answered differently) |
| `C` | collision: the frame could not be sent after retries |
| `X` | no bus power |
| `?` | command not understood |

## Interface to host (monitor, when `M1`)

| Line | Meaning |
|---|---|
| `F<hex> [t]` | forward frame sent by another transmitter; `t` = timestamp in µs (optional, with cap `ts`) |
| `R<hh> [t]` | backward frame seen on the bus |
| `!E [t]` | framing error seen |
| `!P0` / `!P1` | bus power lost / restored |
| `# text` | free text (debug), ignored by hosts |

## Example session

```
> V
< V 1 f24,sniff,back,ts my-rp2040-dali 0.3
> M1
< K
> SFF90
< A04
> TA5FF
< N
< F0280 1203344      (another controller set A1 to level 128)
```

## Writing firmware

- Bus side: a DALI PHY with an opto-isolated receiver and a transistor that pulls the bus low (current limited by the bus power supply). Open-hardware designs are linked in [hardware.md](hardware.md).
- Receive: time the edges with a timer capture and accept half bits of 333..500 µs and full bits of 667..1000 µs. `dali_sim/protocol/manchester.py` shows the decoder logic in Python.
- Transmit: wait for idle plus the settling time of the priority, send, and compare the bus level with the sent level at each half bit to detect collisions.
- Answers (`B`): keep the time of the last received forward frame and start the backward frame within the window.

Please add your firmware to the "Interfaces" list in [hardware.md](hardware.md) with a pull request.
