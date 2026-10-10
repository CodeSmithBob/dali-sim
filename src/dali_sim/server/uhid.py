"""Fake USB DALI sticks on Linux, through the kernel's ``/dev/uhid``.

The simulator creates a HID device with the USB vendor/product ID of a real
DALI stick.  The kernel gives it a ``/dev/hidrawN`` node exactly like the
real stick, so DALI master software opens it without knowing it is talking
to the simulator.  Reports the master writes go to an interface emulator
(``dali_sim.emulators``) backed by the simulated bus; the emulator's answers
go back as input reports.

Needs Linux with the ``uhid`` module (``sudo modprobe uhid``) and write
access to ``/dev/uhid``.  The master must run on the same machine (or in a
container that is given the hidraw node).
"""

from __future__ import annotations

import asyncio
import os
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..adapters import hasseb as H
from ..adapters import tridonic as T
from ..adapters.hid_transport import find_hidraw
from ..emulators import HassebEmulator, HidEmulator, TridonicEmulator

if TYPE_CHECKING:  # pragma: no cover
    from ..sim import Simulator

# --- linux/uhid.h --------------------------------------------------------------
UHID_DESTROY, UHID_START, UHID_STOP, UHID_OPEN, UHID_CLOSE, UHID_OUTPUT = 1, 2, 3, 4, 5, 6
UHID_GET_REPORT, UHID_GET_REPORT_REPLY, UHID_CREATE2, UHID_INPUT2 = 9, 10, 11, 12
UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 13, 14
DATA_MAX = 4096
BUS_USB = 0x03
EIO = 5

_CREATE2 = struct.Struct("<I128s64s64sHHIIII4096s")
EVENT_SIZE = _CREATE2.size  # 4376: the type plus the largest union member


def _event(fmt: str, *values) -> bytes:
    data = struct.pack(fmt, *values)
    return data + bytes(EVENT_SIZE - len(data))


def create2_event(name: str, vid: int, pid: int, descriptor: bytes, uniq: str = "") -> bytes:
    return _CREATE2.pack(UHID_CREATE2, name.encode()[:127], b"dali-sim", uniq.encode()[:63], len(descriptor),
                         BUS_USB, vid, pid, 0x0100, 0, descriptor)


def input2_event(data: bytes) -> bytes:
    if len(data) > DATA_MAX:
        raise ValueError("input report too large")
    return _event(f"<IH{len(data)}s", UHID_INPUT2, len(data), data)


def destroy_event() -> bytes:
    return _event("<I", UHID_DESTROY)


def parse_event(buf: bytes) -> tuple[int, dict]:
    """Decode the events the kernel sends to a uhid device."""
    (typ,) = struct.unpack_from("<I", buf, 0)
    if typ == UHID_OUTPUT:
        size, rtype = struct.unpack_from("<HB", buf, 4 + DATA_MAX)
        return typ, {"data": bytes(buf[4:4 + size]), "rtype": rtype}
    if typ == UHID_GET_REPORT:
        rid, rnum, rtype = struct.unpack_from("<IBB", buf, 4)
        return typ, {"id": rid, "rnum": rnum, "rtype": rtype}
    if typ == UHID_SET_REPORT:
        rid, rnum, rtype, size = struct.unpack_from("<IBBH", buf, 4)
        return typ, {"id": rid, "rnum": rnum, "rtype": rtype, "data": bytes(buf[12:12 + size])}
    return typ, {}


def _vendor_descriptor(size: int, report_id: int | None = None) -> bytes:
    """Vendor-defined HID report descriptor: one input and one output report
    of ``size`` bytes (after the report ID, if any)."""
    d = bytes([0x06, 0x00, 0xFF, 0x09, 0x01, 0xA1, 0x01])  # usage page vendor, usage 1, application
    if report_id is not None:
        d += bytes([0x85, report_id])
    d += bytes([0x15, 0x00, 0x26, 0xFF, 0x00, 0x75, 0x08])  # logical 0..255, 8-bit fields
    d += bytes([0x95, size, 0x09, 0x01, 0x81, 0x02])  # input report
    d += bytes([0x95, size, 0x09, 0x01, 0x91, 0x02])  # output report
    return d + bytes([0xC0])


@dataclass(frozen=True)
class StickModel:
    name: str
    vid: int
    pid: int
    descriptor: bytes
    emulator: type[HidEmulator]


STICKS = {
    "tridonic": StickModel("Tridonic DALI USB (dali-sim)", T.VID, T.PID, _vendor_descriptor(64), TridonicEmulator),
    # The older Lunatone DALI USB uses the same IDs and protocol.
    "lunatone": StickModel("Lunatone DALI USB (dali-sim)", T.VID, T.PID, _vendor_descriptor(64), TridonicEmulator),
    "hasseb": StickModel("hasseb DALI Master (dali-sim)", H.VID, H.PID, _vendor_descriptor(9, H.REPORT_ID),
                         HassebEmulator),
}


class FakeUsbStick:
    """One fake stick.  ``fd`` lets tests pass a socket instead of /dev/uhid."""

    def __init__(self, sim: Simulator, kind: str, path: str = "/dev/uhid", fd: int | None = None,
                 line: int = 1) -> None:
        if kind not in STICKS:
            raise ValueError(f"unknown stick {kind!r}; choose from {', '.join(STICKS)}")
        self.sim = sim
        self.kind = kind
        self.line = line
        #: Different per line, so a master with several sticks can tell them apart.
        self.serial = 0x0DA11000 + line
        self.uniq = f"dali-sim-{kind}-line{line}"
        self.model = STICKS[kind]
        self.path = path
        self.fd = fd
        self._own_fd = fd is None
        self.emulator: HidEmulator | None = None
        self.opened_by_host = False

    async def start(self) -> None:
        if self.fd is None:
            try:
                self.fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC | os.O_NONBLOCK)
            except FileNotFoundError:
                raise RuntimeError(f"{self.path} not found: run 'sudo modprobe uhid' (Linux only)") from None
            except PermissionError:
                raise RuntimeError(f"no permission for {self.path}: run as root or see docs/hardware.md") from None
        m = self.model
        if m.emulator is TridonicEmulator:
            self.emulator = TridonicEmulator(self.sim.bus, serial=self.serial)
        else:
            self.emulator = m.emulator(self.sim.bus)
        self.emulator.on_report = self._input
        name = m.name if self.line == 1 else m.name.replace("(dali-sim)", f"(dali-sim line {self.line})")
        os.write(self.fd, create2_event(name, m.vid, m.pid, m.descriptor, uniq=self.uniq))
        asyncio.get_running_loop().add_reader(self.fd, self._readable)

    async def stop(self) -> None:
        if self.fd is None:
            return
        asyncio.get_running_loop().remove_reader(self.fd)
        try:
            os.write(self.fd, destroy_event())
        except OSError:
            pass
        if self._own_fd:
            os.close(self.fd)
        self.fd = None

    def rebind(self, sim: Simulator) -> None:
        """Follow another simulator (the scenario was replaced)."""
        self.sim = sim
        if self.emulator:
            self.emulator.rebind(sim.bus)

    def hidraw_paths(self) -> list[str]:
        """hidraw nodes of this stick (other sticks may share the USB ID)."""
        out = []
        for path in find_hidraw(self.model.vid, self.model.pid):
            try:
                text = open(f"/sys/class/hidraw/{os.path.basename(path)}/device/uevent", encoding="ascii").read()
            except OSError:
                continue
            if f"HID_UNIQ={self.uniq}" in text:
                out.append(path)
        return out

    def _input(self, report: bytes) -> None:
        if self.fd is not None:
            try:
                os.write(self.fd, input2_event(report))
            except OSError as exc:
                self.sim.bus.info(f"fake {self.kind} stick: cannot send report: {exc}")

    def _readable(self) -> None:
        try:
            buf = os.read(self.fd, EVENT_SIZE)
        except BlockingIOError:
            return
        except OSError:
            return
        if len(buf) < 4:
            return
        typ, ev = parse_event(buf)
        if typ == UHID_OUTPUT and self.emulator:
            self.emulator.write(ev["data"])
        elif typ == UHID_OPEN:
            self.opened_by_host = True
        elif typ == UHID_CLOSE:
            self.opened_by_host = False
        elif typ == UHID_GET_REPORT:
            # No feature reports: answer with an error so the caller does not hang.
            os.write(self.fd, _event("<IIHH", UHID_GET_REPORT_REPLY, ev["id"], EIO, 0))
        elif typ == UHID_SET_REPORT:
            if self.emulator and ev["data"]:
                self.emulator.write(ev["data"])
            os.write(self.fd, _event("<IIH", UHID_SET_REPORT_REPLY, ev["id"], 0))
