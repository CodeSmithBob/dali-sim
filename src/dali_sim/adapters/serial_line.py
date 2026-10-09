"""Adapter for interfaces that speak the dali-sim serial line protocol
(docs/serial-protocol.md): DIY firmware on a microcontroller, or another
dali-sim's virtual serial port."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from ..bus import NO_ANSWER, Reply
from ..protocol.frames import Frame
from .base import Adapter, AdapterError, Capabilities, Observed


class LineTransport:
    """Line-based I/O over pyserial (a thread reads, asyncio consumes)."""

    def __init__(self, path: str, baudrate: int = 115200) -> None:
        self.path = path
        self.baudrate = baudrate
        self._ser = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.on_line = None  # callable(str)

    def open(self, loop: asyncio.AbstractEventLoop) -> None:
        try:
            import serial  # pyserial
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise AdapterError("pyserial is needed: pip install 'dali-sim[serial]'") from exc
        self._ser = serial.serial_for_url(self.path, baudrate=self.baudrate, timeout=0.2)
        self._stop.clear()

        def run() -> None:
            buf = b""
            while not self._stop.is_set():
                try:
                    chunk = self._ser.read(256)
                except Exception:
                    break
                if not chunk:
                    continue
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("ascii", "replace").strip()
                    if text and self.on_line:
                        loop.call_soon_threadsafe(self.on_line, text)

        self._thread = threading.Thread(target=run, name=f"serial-{self.path}", daemon=True)
        self._thread.start()

    def write(self, text: str) -> None:
        assert self._ser is not None
        self._ser.write((text + "\n").encode("ascii"))

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self._ser:
            self._ser.close()


class SerialLineAdapter(Adapter):
    name = "serial"
    caps = Capabilities(forward24=True, sniff=True, send_backward=True, timestamps=True, native_twice=True,
                        notes="dali-sim serial line protocol v1 (DIY firmware); real capabilities come from 'V'")

    def __init__(self, path: str = "/dev/ttyUSB0", baudrate: int = 115200, **options: Any) -> None:
        super().__init__(**options)
        self.path = path
        self.io = LineTransport(path, baudrate)
        self._responses: asyncio.Queue[str] = asyncio.Queue()
        self.caps = Capabilities(**vars(type(self).caps))
        self.firmware = ""

    async def _open(self) -> None:
        loop = asyncio.get_running_loop()
        self.io.on_line = self._line
        self.io.open(loop)
        v = await self._cmd("V", timeout=2.0)
        parts = v.split()
        if not parts or parts[0] != "V" or len(parts) < 3 or parts[1] != "1":
            raise AdapterError(f"unexpected version answer {v!r}")
        caps = set(parts[2].split(","))
        self.caps.forward24 = "f24" in caps
        self.caps.sniff = "sniff" in caps
        self.caps.send_backward = "back" in caps
        self.caps.timestamps = "ts" in caps
        self.firmware = " ".join(parts[3:])
        if self.caps.sniff:
            await self._cmd("M1")

    async def _close(self) -> None:
        self.io.close()

    def _line(self, text: str) -> None:
        kind = text[0]
        if kind == "F":
            hexpart = text[1:].split()[0]
            try:
                self.emit(Observed(Frame.from_bytes(bytes.fromhex(hexpart)), "forward"))
            except ValueError:
                pass
        elif kind == "R":
            self.emit(Observed(Frame.backward(int(text[1:3], 16)), "backward"))
        elif kind == "!":
            if text.startswith("!E"):
                self.emit(Observed(None, "framing_error"))
            else:
                self.emit(Observed(None, "info", text=text))
        elif kind == "#":
            return
        else:
            self._responses.put_nowait(text)

    async def _cmd(self, text: str, timeout: float = 1.0) -> str:
        self.io.write(text)
        try:
            return await asyncio.wait_for(self._responses.get(), timeout)
        except asyncio.TimeoutError:
            raise AdapterError(f"no response to {text!r} from {self.path}") from None

    async def _transact(self, frame: Frame, expect_answer: bool, twice: bool) -> Reply:
        kind = "T" if twice else "S" if expect_answer else "Q"
        cmd = kind + frame.to_bytes().hex().upper()
        resp = await self._cmd(cmd)
        return parse_answer(resp)

    async def _send_backward(self, value: int) -> None:
        resp = await self._cmd(f"B{value:02X}")
        if resp == "L":
            raise AdapterError("backward frame too late")


def parse_answer(resp: str) -> Reply:
    if resp.startswith("A") and len(resp) >= 3:
        return Reply("answer", int(resp[1:3], 16))
    if resp == "E":
        return Reply("framing_error")
    if resp in ("N", "K"):
        return NO_ANSWER
    if resp == "C":
        raise AdapterError("collision: frame not sent")
    if resp == "X":
        raise AdapterError("no bus power")
    raise AdapterError(f"unexpected response {resp!r}")
