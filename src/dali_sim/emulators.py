"""Interface emulators: the *device side* of the USB/serial protocols, backed
by a simulated bus.

They let software written for a real interface talk to the simulator:

* serial protocols (dali-sim line protocol, AtxLED HAT, Lunatone LUBA) are
  offered on a virtual serial port: ``dali-sim serve --pty LINK --pty-protocol atxled``;
* the HID protocols (hasseb, Tridonic/Lunatone 17B5:0020) are used by the
  test-suite to check the drivers byte for byte.  A Linux ``/dev/uhid``
  frontend that makes them appear as real USB devices is a welcome
  contribution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .bus import Bus, BusRecord
from .protocol.frames import Frame

Out = Callable[[bytes], None]


def _spawn(coro) -> None:
    asyncio.ensure_future(coro)


# ---------------------------------------------------------------------------
# Serial-style emulators: feed(bytes) in, out(bytes) for responses
# ---------------------------------------------------------------------------

class SerialEmulator:
    name = "serial"

    def __init__(self, bus: Bus, out: Out | None = None) -> None:
        self.bus = bus
        self.out: Out = out or (lambda b: None)
        self._buf = b""
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        bus.listeners.append(self._record)

    def start(self) -> None:
        self._task = asyncio.ensure_future(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        try:
            self.bus.listeners.remove(self._record)
        except ValueError:
            pass

    def feed(self, data: bytes) -> None:
        self._queue.put_nowait(data)

    async def _run(self) -> None:
        while True:
            data = await self._queue.get()
            try:
                await self.handle(data)
            except Exception as exc:  # keep serving
                self.bus.info(f"{self.name} emulator: {exc!r}")

    async def handle(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def _record(self, rec: BusRecord) -> None:
        pass


class LineEmulator(SerialEmulator):
    async def handle(self, data: bytes) -> None:
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            text = line.decode("ascii", "replace").strip()
            if text:
                for resp in await self.line(text):
                    self.out((resp + "\n").encode("ascii"))

    async def line(self, text: str) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError


class DaliSimLineEmulator(LineEmulator):
    """The dali-sim serial line protocol (docs/serial-protocol.md)."""

    name = "dalisim"

    def __init__(self, bus: Bus, out: Out | None = None) -> None:
        super().__init__(bus, out)
        self.priority = 3
        self.monitor = False

    def _record(self, rec: BusRecord) -> None:
        if not self.monitor or rec.source == self.name:
            return
        t = int(rec.t * 1e6)
        line = None
        if rec.kind in ("forward", "event") and rec.frame is not None:
            line = f"F{rec.frame.hex().replace(' ', '')} {t}"
        elif rec.kind == "backward" and rec.frame is not None:
            line = f"R{rec.frame.value:02X} {t}"
        elif rec.kind == "framing_error":
            line = f"!E {t}"
        elif rec.kind == "power":
            line = "!P1" if "restored" in rec.text else "!P0"
        if line:
            self.out((line + "\n").encode())

    async def line(self, text: str) -> list[str]:
        from .server.tcp import parse_frame

        c, arg = text[0].upper(), text[1:].strip()
        if c == "V":
            return ["V 1 f24,sniff,ts dali-sim-pty"]
        if c == "M":
            self.monitor = arg == "1"
            return ["K"]
        if c == "P":
            if arg.isdigit() and 1 <= int(arg) <= 5:
                self.priority = int(arg)
                return ["K"]
            return ["?"]
        if c in ("S", "T", "Q"):
            try:
                frame = parse_frame(arg)
            except ValueError:
                return ["?"]
            if not self.bus.powered:
                return ["X"]
            r = await self.bus.send([frame, frame] if c == "T" else [frame], source=self.name,
                                    priority=self.priority, expect_answer=False if c == "Q" else None)
            if c == "Q":
                return ["K"]
            if r.answered:
                return [f"A{r.value:02X}"]
            return ["E" if r.collision else "N"]
        if c == "B":
            return ["L"]
        return ["?"]


class AtxLedEmulator(LineEmulator):
    """AtxLED DALI HAT line protocol."""

    name = "atxled"

    async def line(self, text: str) -> list[str]:
        prefix, hexpart = text[0], text[1:]
        try:
            frame = Frame.from_bytes(bytes.fromhex(hexpart))
        except ValueError:
            return []
        if not self.bus.powered:
            return ["X"]
        if prefix == "t":
            frames = [frame, frame]
        elif prefix in ("h", "l"):
            frames = [frame]
        else:
            return ["X"]  # 8-bit and 25-bit frames are not emulated
        r = await self.bus.send(frames, source=self.name)
        one = f"J{r.value:02X}" if r.answered else "N"
        return [one] * len(frames)


class LubaEmulator(SerialEmulator):
    """Lunatone LUBA interface."""

    name = "luba"

    def __init__(self, bus: Bus, out: Out | None = None) -> None:
        from .adapters.lunatone import LubaParser

        super().__init__(bus, out)
        self.parser = LubaParser()
        self.settings = bytes([0, 0, 0])
        self.tx_id = 0
        self.monitor = True

    def _event(self, etype: int, info: int, rest: bytes) -> None:
        from .adapters.lunatone import EVENT, packet

        tick = int(self.bus.now() * 1000) & 0xFFFF
        self.out(packet(EVENT, [tick >> 8, tick & 0xFF, 0, (etype << 6) | info, *rest]))

    def _record(self, rec: BusRecord) -> None:
        if not self.monitor or rec.source == self.name or rec.frame is None:
            return
        if rec.kind in ("forward", "event", "backward"):
            self._event(2, rec.frame.bits, rec.frame.to_bytes())

    async def handle(self, data: bytes) -> None:
        from .adapters import lunatone as L

        for cmd, payload in self.parser.feed(data):
            if cmd == L.CMD_SETTINGS:
                self.settings = bytes(payload[:3])
                self.out(L.packet(L.RSP_SETTINGS, list(self.settings)))
            elif cmd == L.CMD_DEVICE_INFO:
                info = (0).to_bytes(6, "big") + (0x0D417151).to_bytes(8, "big") + bytes([1, 1]) + \
                    (24000000).to_bytes(4, "big")
                self.out(L.packet(L.RSP_DEVICE_INFO, list(info)))
            elif cmd == L.CMD_TX:
                bits, mode = payload[1], payload[2]
                if bits not in (16, 24):
                    self.out(L.packet(L.RSP_TX, [1]))
                    continue
                frame = Frame(bits, int.from_bytes(payload[3:3 + bits // 8], "big"))
                self.tx_id = self.tx_id % 255 + 1
                self.out(L.packet(L.RSP_TX, [self.tx_id, 0]))
                twice = bool(mode & 0x80)
                prio = max(1, min(5, mode & 0x07 or 3))
                r = await self.bus.send([frame, frame] if twice else [frame], source=self.name, priority=prio)
                for _ in range(2 if twice else 1):
                    self._event(0, 0, bytes([self.tx_id]) + frame.to_bytes())
                if r.answered:
                    self._event(2, 8, bytes([r.value]))
                elif r.collision:
                    self._event(2, L.INFO_FRAMING_ERROR, b"")


SERIAL_EMULATORS = {"dalisim": DaliSimLineEmulator, "atxled": AtxLedEmulator, "luba": LubaEmulator}


# ---------------------------------------------------------------------------
# HID emulators: objects with the HidTransport interface
# ---------------------------------------------------------------------------

class HidEmulator:
    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self.on_report: Callable[[bytes], None] | None = None
        self.written: list[bytes] = []

    def open(self, loop: asyncio.AbstractEventLoop) -> str:
        return "emulator"

    def close(self) -> None:
        pass

    def write(self, data: bytes) -> None:
        self.written.append(bytes(data))
        _spawn(self.handle(bytes(data)))

    def report(self, data: bytes) -> None:
        if self.on_report:
            self.on_report(data)

    async def handle(self, data: bytes) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class HassebEmulator(HidEmulator):
    async def handle(self, data: bytes) -> None:
        from .adapters import hasseb as H

        cmd, seq = data[1], data[2]
        if cmd == H.CMD_FIRMWARE:
            self.report(bytes([H.REPORT_ID, H.CMD_FIRMWARE, seq, 2, 6, 0, 0, 0, 0, 0]))
            return
        if cmd != H.CMD_FRAME:
            return
        frame = Frame.forward16(data[7], data[8])
        twice, expect = data[6] > 0, data[4] == 1
        r = await self.bus.send([frame, frame] if twice else [frame], source="hasseb-usb")
        if not expect:
            return
        if r.answered:
            self.report(bytes([H.REPORT_ID, H.CMD_FRAME, seq, H.OK, 1, r.value, 0, 0, 0, 0]))
        elif r.collision:
            self.report(bytes([H.REPORT_ID, H.CMD_FRAME, seq, H.INVALID, 0, 0, 0, 0, 0, 0]))
        else:
            self.report(bytes([H.REPORT_ID, H.CMD_FRAME, seq, H.NO_ANSWER_ST, 0, 0, 0, 0, 0, 0]))


class TridonicEmulator(HidEmulator):
    def __init__(self, bus: Bus) -> None:
        super().__init__(bus)
        bus.listeners.append(self._record)

    def _rep(self, origin: int, typ: int, frame4: bytes, seq: int) -> None:
        body = bytes([origin, typ]) + frame4 + bytes([0, 0, seq])
        self.report(body + bytes(64 - len(body)))

    def _record(self, rec: BusRecord) -> None:
        from .adapters import tridonic as T

        if rec.source == "tridonic-usb" or rec.frame is None:
            return
        typ = {16: T.T_16, 24: T.T_24, 8: T.T_8}.get(rec.frame.bits)
        if typ:
            self._rep(T.ORIGIN_OBSERVE, typ, rec.frame.value.to_bytes(4, "big"), 0)

    async def handle(self, data: bytes) -> None:
        from .adapters import tridonic as T

        cmd = data[0]
        if cmd == T.CMD_INIT:
            if data[1] == 0x00:
                self.report(bytes([T.ORIGIN_INFO, 0, 0, 1, 7]) + bytes(59))
            else:
                self.report(bytes([T.ORIGIN_INFO, 0x12, 0x34, 0x56, 0x78]) + bytes(59))
            return
        if cmd != T.CMD_SEND:
            return
        seq, ctrl, mode = data[1], data[2], data[3]
        bits = {3: 16, 6: 24}.get(mode)
        if bits is None:
            return
        frame = Frame(bits, int.from_bytes(data[4:8], "big") & ((1 << bits) - 1))
        twice = bool(ctrl & T.CTRL_TWICE)
        r = await self.bus.send([frame, frame] if twice else [frame], source="tridonic-usb")
        for _ in range(2 if twice else 1):
            self._rep(T.ORIGIN_RESPONSE, T.T_16 if bits == 16 else T.T_24, frame.value.to_bytes(4, "big"), seq)
        if r.answered:
            self._rep(T.ORIGIN_RESPONSE, T.T_8, bytes([0, 0, 0, r.value]), seq)
        elif r.collision:
            self._rep(T.ORIGIN_RESPONSE, T.T_INFO, bytes([0, 0, 0, T.BUS_FRAMING_ERROR]), seq)
        else:
            self._rep(T.ORIGIN_RESPONSE, T.T_NONE, bytes(4), seq)
