"""A virtual serial port that makes the simulator look like a serial DALI
interface.  Protocols: ``dalisim`` (docs/serial-protocol.md, default),
``atxled`` (AtxLED DALI HAT) and ``luba`` (Lunatone LUBA).

Software that talks to such an interface can open the pty instead of real
hardware.  Linux and macOS.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from ..emulators import SERIAL_EMULATORS

if TYPE_CHECKING:  # pragma: no cover
    from ..sim import Simulator


class PtyServer:
    def __init__(self, sim: Simulator, link: str | None = "/tmp/dali-sim-tty", protocol: str = "dalisim") -> None:
        if protocol not in SERIAL_EMULATORS:
            raise ValueError(f"unknown pty protocol {protocol!r}; choose from {', '.join(SERIAL_EMULATORS)}")
        self.sim = sim
        self.link = link
        self.protocol = protocol
        self.master: int | None = None
        self.slave_name = ""
        self.emulator = None

    async def start(self) -> str:
        import tty

        master, slave = os.openpty()
        tty.setraw(slave)
        self.master = master
        self.slave_name = os.ttyname(slave)
        self._slave = slave  # keep open so the port stays usable between clients
        os.set_blocking(master, False)
        if self.link:
            try:
                if os.path.islink(self.link):
                    os.unlink(self.link)
                os.symlink(self.slave_name, self.link)
            except OSError:
                self.link = None
        self.emulator = SERIAL_EMULATORS[self.protocol](self.sim.bus, self._write)
        self.emulator.start()
        asyncio.get_running_loop().add_reader(master, self._readable)
        return self.link or self.slave_name

    async def stop(self) -> None:
        if self.emulator:
            self.emulator.stop()
        if self.master is not None:
            asyncio.get_running_loop().remove_reader(self.master)
            os.close(self.master)
            os.close(self._slave)
            self.master = None
        if self.link and os.path.islink(self.link):
            os.unlink(self.link)

    def _write(self, data: bytes) -> None:
        if self.master is not None:
            try:
                os.write(self.master, data)
            except OSError:
                pass

    def _readable(self) -> None:
        try:
            data = os.read(self.master, 1024)
        except OSError:
            return
        if data and self.emulator:
            self.emulator.feed(data)
