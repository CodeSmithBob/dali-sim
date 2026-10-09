"""Lunatone interfaces speaking LUBA (Lunatone's published "LUBA protocol",
used over RS232 and USB virtual COM ports, e.g. DALI RS232 LUBA and DALI-2
USB variants).  The older Lunatone DALI USB (USB ID 17B5:0020, HID) uses
the same HID protocol as the Tridonic DALI USB: use ``lunatone:/dev/hidrawN``
or ``tridonic`` for it.

LUBA packets (serial 38400 8N1)::

    0x59 ('Y'), command, length, payload[length], checksum (XOR of command, length and payload)

    0x32 / 0x33  add a DALI frame to the transmit buffer / answer (2 bytes: id, or 1 byte: error)
                 payload: line, bit count, mode (bit 7 send twice, bits 0..2 priority), data[4]
    0x31         event: time tick (2), line, status (bits 7..6 type, 5..0 info), ...
                 type 0 = frame transmitted (then: id, frame bytes)
                 type 2 = frame received (info = bit count, 62 = start/stop only, 63 = framing error)
    0x2A / 0x2B  write/read settings (mode, event filter, hardware)
    0x20 / 0x21  query device information

Facts taken from open-source drivers that cite Lunatone's LUBA document (see
docs/sources.md).  NOT YET TESTED ON HARDWARE by the dali-sim authors.
"""

from __future__ import annotations

import asyncio
from functools import reduce
from operator import xor
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities, Observed
from .serial_line import LineTransport

START = 0x59
CMD_DEVICE_INFO, RSP_DEVICE_INFO = 0x20, 0x21
CMD_SETTINGS, RSP_SETTINGS = 0x2A, 0x2B
EVENT = 0x31
CMD_TX, RSP_TX = 0x32, 0x33
EV_TRANSMITTED, EV_RECEIVED = 0, 2
INFO_START_STOP_ONLY, INFO_FRAMING_ERROR = 62, 63
#: Settings written at start: normal mode; report transmitted and received frames.
SETTINGS = (0x00, 0x12, 0x00)


def packet(cmd: int, payload: bytes | list[int]) -> bytes:
    body = bytes([cmd, len(payload), *payload])
    return bytes([START]) + body + bytes([reduce(xor, body)])


def tx_packet(frame: Frame, twice: bool, priority: int) -> bytes:
    data = frame.to_bytes() + bytes(4 - len(frame.to_bytes()))
    mode = (0x80 if twice else 0) | (priority & 0x07)
    return packet(CMD_TX, [0, frame.bits, mode, *data])


class LubaParser:
    """Byte stream to packets: yields (command, payload)."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buf += data
        out = []
        while True:
            while self.buf and self.buf[0] != START:
                del self.buf[0]
            if len(self.buf) < 4:
                return out
            length = self.buf[2]
            if len(self.buf) < length + 4:
                return out
            pkt = bytes(self.buf[: length + 4])
            del self.buf[: length + 4]
            if reduce(xor, pkt[1:-1]) == pkt[-1]:
                out.append((pkt[1], pkt[3:-1]))


class SerialBytes(LineTransport):
    """Raw byte access on top of LineTransport's serial port handling."""

    on_bytes = None

    def open(self, loop: asyncio.AbstractEventLoop) -> None:  # type: ignore[override]
        import threading

        try:
            import serial
        except ImportError as exc:  # pragma: no cover
            raise AdapterError("pyserial is needed: pip install 'dali-sim[serial]'") from exc
        self._ser = serial.serial_for_url(self.path, baudrate=self.baudrate, timeout=0.05)
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                try:
                    chunk = self._ser.read(256)
                except Exception:
                    break
                if chunk and self.on_bytes:
                    loop.call_soon_threadsafe(self.on_bytes, chunk)

        self._thread = threading.Thread(target=run, name=f"serial-{self.path}", daemon=True)
        self._thread.start()

    def write_bytes(self, data: bytes) -> None:
        self._ser.write(data)


class LunatoneAdapter(Adapter):
    name = "lunatone"
    caps = Capabilities(forward24=True, sniff=True, send_backward=False, native_twice=True, timestamps=True,
                        notes="Lunatone LUBA over RS232 / USB VCOM; protocol from public sources, needs hardware testing")

    def __init__(self, path: str = "/dev/ttyACM0", baudrate: int = 38400, priority: int = 3, **options: Any) -> None:
        super().__init__(**options)
        self.io = SerialBytes(path, baudrate)
        self.parser = LubaParser()
        self.priority = priority
        self._packets: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
        self._in_tx = False
        self.info: dict[str, Any] = {}

    async def _open(self) -> None:
        self.io.on_bytes = self._bytes
        self.io.open(asyncio.get_running_loop())
        self.io.write_bytes(packet(CMD_SETTINGS, list(SETTINGS)))
        cmd, payload = await self._expect(RSP_SETTINGS, 1.0)
        if tuple(payload[:2]) != SETTINGS[:2]:
            raise AdapterError(f"{self.name}: interface did not accept the settings")
        self.io.write_bytes(packet(CMD_DEVICE_INFO, [0]))
        try:
            _, p = await self._expect(RSP_DEVICE_INFO, 1.0)
            if len(p) >= 20:
                self.info = {"gtin": int.from_bytes(p[0:6], "big"), "serial": p[6:14].hex(),
                             "article": int.from_bytes(p[16:20], "big")}
        except AdapterError:
            pass

    async def _close(self) -> None:
        self.io.close()

    def _bytes(self, data: bytes) -> None:
        for cmd, payload in self.parser.feed(data):
            if cmd == EVENT and not self._in_tx:
                self._observe(payload)
            else:
                self._packets.put_nowait((cmd, payload))

    def _observe(self, payload: bytes) -> None:
        if len(payload) < 4:
            return
        etype, info = payload[3] >> 6, payload[3] & 0x3F
        if etype != EV_RECEIVED:
            return
        if info == INFO_FRAMING_ERROR:
            self.emit(Observed(None, "framing_error"))
        elif 1 <= info <= 32:
            frame = Frame(info, int.from_bytes(payload[4:4 + (info + 7) // 8], "big") >> (8 * ((info + 7) // 8) - info))
            self.emit(Observed(frame, "backward" if info == 8 else "forward"))

    async def _expect(self, want: int, timeout: float) -> tuple[int, bytes]:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while True:
            left = end - loop.time()
            if left <= 0:
                raise AdapterError(f"{self.name}: no response 0x{want:02X}")
            try:
                cmd, payload = await asyncio.wait_for(self._packets.get(), left)
            except asyncio.TimeoutError:
                raise AdapterError(f"{self.name}: no response 0x{want:02X}") from None
            if cmd == want:
                return cmd, payload

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        while not self._packets.empty():
            self._packets.get_nowait()
        self._in_tx = True
        try:
            self.io.write_bytes(tx_packet(frame, twice, self.priority))
            _, rsp = await self._expect(RSP_TX, 1.0)
            if len(rsp) == 1:
                raise AdapterError(f"{self.name}: transmission refused, error {rsp[0]}")
            confirms = 2 if twice else 1
            loop = asyncio.get_running_loop()
            while confirms:
                _, ev = await self._expect(EVENT, 1.0)
                if len(ev) >= 4 and ev[3] >> 6 == EV_TRANSMITTED:
                    confirms -= 1
            if not expect_answer:
                return NO_ANSWER
            end = loop.time() + 0.03  # the answer window ends ~22 ms after the frame
            while True:
                left = end - loop.time()
                if left <= 0:
                    return NO_ANSWER
                try:
                    _, ev = await self._expect(EVENT, left)
                except AdapterError:
                    return NO_ANSWER
                if len(ev) < 4 or ev[3] >> 6 != EV_RECEIVED:
                    continue
                info = ev[3] & 0x3F
                if info == 8 and len(ev) >= 5:
                    return Reply("answer", ev[4])
                if info in (INFO_FRAMING_ERROR, INFO_START_STOP_ONLY):
                    return Reply("framing_error")
        finally:
            self._in_tx = False
