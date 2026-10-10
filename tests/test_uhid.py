"""Fake USB sticks: the uhid side is tested against a socket that plays the
kernel, and the real stick drivers talk through it."""

import asyncio
import socket
import struct

import pytest

from dali_sim.adapters.hasseb import HassebAdapter
from dali_sim.adapters.tridonic import TridonicAdapter
from dali_sim.config import build
from dali_sim.controller import Controller
from dali_sim.server import uhid as U


class FakeKernel:
    """The kernel end of /dev/uhid, exposed to a driver as a hidraw node."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.created: dict | None = None
        self.on_report = None
        sock.setblocking(False)

    def open(self, loop):
        loop.add_reader(self.sock.fileno(), self._readable)
        self.sock.send(U._event("<I", U.UHID_START))
        self.sock.send(U._event("<I", U.UHID_OPEN))
        return "/dev/hidraw-fake"

    def close(self):
        asyncio.get_running_loop().remove_reader(self.sock.fileno())

    def _readable(self):
        buf = self.sock.recv(U.EVENT_SIZE)
        assert len(buf) == U.EVENT_SIZE
        (typ,) = struct.unpack_from("<I", buf)
        if typ == U.UHID_CREATE2:
            f = U._CREATE2.unpack(buf)
            self.created = {"name": f[1].rstrip(b"\0").decode(), "rd_size": f[4], "bus": f[5], "vendor": f[6],
                            "product": f[7], "rd": f[10][: f[4]]}
        elif typ == U.UHID_INPUT2:
            (size,) = struct.unpack_from("<H", buf, 4)
            if self.on_report:
                self.on_report(bytes(buf[6:6 + size]))

    def write(self, data: bytes):  # hidraw write -> UHID_OUTPUT
        ev = bytearray(U.EVENT_SIZE)
        struct.pack_into("<I", ev, 0, U.UHID_OUTPUT)
        ev[4:4 + len(data)] = data
        struct.pack_into("<HB", ev, 4 + U.DATA_MAX, len(data), 1)
        self.sock.send(bytes(ev))


def sim():
    return build({"gear": [{"name": "lamp", "short_address": 1, "type": "led"}],
                  "devices": [{"name": "btn", "short_address": 2, "instances": ["push_button"]}]}, manual_clock=True)


@pytest.mark.parametrize("kind,adapter_cls,vid,pid", [
    ("tridonic", TridonicAdapter, 0x17B5, 0x0020),
    ("lunatone", TridonicAdapter, 0x17B5, 0x0020),
    ("hasseb", HassebAdapter, 0x04CC, 0x0802),
])
async def test_fake_stick_with_real_driver(kind, adapter_cls, vid, pid):
    s = sim()
    dev_side, kernel_side = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    stick = U.FakeUsbStick(s, kind, fd=dev_side.fileno())
    await stick.start()
    kernel = FakeKernel(kernel_side)
    adapter = adapter_cls(transport=kernel)
    await adapter.open()
    await asyncio.sleep(0)
    assert kernel.created["vendor"] == vid and kernel.created["product"] == pid and kernel.created["bus"] == 3
    assert kernel.created["rd"] == U.STICKS[kind].descriptor
    assert stick.opened_by_host

    c = Controller(adapter)
    assert await c.query(1, 0x91) == 0xFF
    await c.set_max_level(1, 180)  # send-twice handled inside the stick
    assert s.units["lamp"].max_level == 180
    await c.dapc(1, 90)
    assert await c.query_actual_level(1) == 90
    if adapter.caps.forward24:
        assert await c.device_query(0x05, 0xFF, 0x35) == 1
    await adapter.close()
    await stick.stop()
    dev_side.close()
    kernel_side.close()


def test_event_sizes_match_linux_uhid_h():
    assert U.EVENT_SIZE == 4376
    assert len(U.input2_event(b"\1\2")) == U.EVENT_SIZE
    assert len(U.create2_event("x", 1, 2, b"\0")) == U.EVENT_SIZE


def test_unknown_stick():
    with pytest.raises(ValueError):
        U.FakeUsbStick(sim(), "nope")
