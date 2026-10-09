"""USB HID access for DALI interfaces.

On Linux the ``/dev/hidrawN`` node is used directly (no extra packages; works
in Docker with ``--device``).  Elsewhere, or when no path is given and the
device has to be found by vendor/product ID, the ``hidapi`` package is used.

Reports are written and read as raw byte strings, exactly as the interface
expects them on the wire (the first byte is the report ID where the device
uses one).
"""

from __future__ import annotations

import asyncio
import glob
import os
import threading
from collections.abc import Callable

from .base import AdapterError


def find_hidraw(vid: int, pid: int) -> list[str]:
    """Linux: hidraw nodes whose USB vendor/product match."""
    want = f"{vid:08X}:{pid:08X}"
    out = []
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            text = open(uevent, encoding="ascii", errors="replace").read().upper()
        except OSError:
            continue
        if want in text:
            out.append("/dev/" + uevent.split("/")[4])
    return out


class HidTransport:
    def __init__(self, path: str | None = None, vid: int | None = None, pid: int | None = None,
                 report_size: int = 64) -> None:
        self.path = path
        self.vid, self.pid = vid, pid
        self.report_size = report_size
        self.on_report: Callable[[bytes], None] | None = None
        self._fd: int | None = None
        self._dev = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def open(self, loop: asyncio.AbstractEventLoop) -> str:
        self._loop = loop
        path = self.path
        if not path and self.vid is not None and os.path.isdir("/sys/class/hidraw"):
            found = find_hidraw(self.vid, self.pid or 0)
            path = found[0] if found else None
        if path and path.startswith("/dev/hidraw"):
            try:
                self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            except OSError as exc:
                raise AdapterError(f"cannot open {path}: {exc} (check permissions / udev rules)") from exc
            loop.add_reader(self._fd, self._readable)
            return path
        return self._open_hidapi(path)

    def _open_hidapi(self, path: str | None) -> str:
        try:
            import hid  # hidapi
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise AdapterError("no hidraw device found and hidapi is not installed: pip install 'dali-sim[hid]'") from exc
        dev = hid.device()
        try:
            if path:
                dev.open_path(path.encode() if isinstance(path, str) else path)
            else:
                dev.open(self.vid, self.pid)
        except OSError as exc:
            raise AdapterError(f"cannot open HID device {path or f'{self.vid:04x}:{self.pid:04x}'}: {exc}") from exc
        self._dev = dev
        self._stop.clear()

        def run() -> None:
            while not self._stop.is_set():
                try:
                    data = dev.read(self.report_size, 200)
                except OSError:
                    break
                if data and self.on_report and self._loop:
                    self._loop.call_soon_threadsafe(self.on_report, bytes(data))

        self._thread = threading.Thread(target=run, name="hid-reader", daemon=True)
        self._thread.start()
        return path or f"{self.vid:04x}:{self.pid:04x}"

    def _readable(self) -> None:
        try:
            data = os.read(self._fd, self.report_size)
        except OSError:
            data = b""
        if data and self.on_report:
            self.on_report(data)

    def write(self, data: bytes) -> None:
        if self._fd is not None:
            os.write(self._fd, data)
        elif self._dev is not None:
            self._dev.write(list(data))
        else:
            raise AdapterError("HID device is not open")

    def close(self) -> None:
        if self._fd is not None:
            if self._loop:
                self._loop.remove_reader(self._fd)
            os.close(self._fd)
            self._fd = None
        if self._dev is not None:
            self._stop.set()
            if self._thread:
                self._thread.join(timeout=1)
            self._dev.close()
            self._dev = None
