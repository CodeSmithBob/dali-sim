"""Human-readable decoding of bus traffic, used by the bus monitor."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import commands as C
from .address import AddrKind, DeviceAddress, GearAddress, InstanceByte, InstKind
from .events import Event, describe_event_info
from .frames import Frame


@dataclass
class Decoded:
    text: str
    target: str = ""
    command: str = ""
    expects_answer: bool = False
    send_twice: bool = False
    is_event: bool = False
    details: dict = field(default_factory=dict)


class FrameDecoder:
    """Stateful decoder: application extended commands (0xE0..0xFF) depend
    on the device type set by the ENABLE DEVICE TYPE command just before."""

    def __init__(self) -> None:
        self._enabled_dt: int | None = None

    def decode(self, frame: Frame) -> Decoded:
        if frame.bits == 16:
            return self._decode16(frame)
        if frame.bits == 24:
            self._enabled_dt = None
            return self._decode24(frame)
        if frame.bits == 8:
            v = frame.value
            return Decoded(f"answer {v} (0x{v:02X}, 0b{v:08b})")
        self._enabled_dt = None
        return Decoded(f"{frame.bits}-bit frame {frame.hex()} (reserved size)")

    # ------------------------------------------------------------------
    def _decode16(self, frame: Frame) -> Decoded:
        a, b = frame.byte(0), frame.byte(1)
        addr = GearAddress.decode(a)
        dt = self._enabled_dt
        self._enabled_dt = None
        if addr.kind == AddrKind.SPECIAL:
            cmd = C.GEAR_SPECIAL.get(a)
            if cmd is None:
                return Decoded(f"reserved special command 0x{a:02X} 0x{b:02X}")
            if a == 0xC1:
                self._enabled_dt = b
                return Decoded(f"ENABLE DEVICE TYPE {b}", command=cmd.name, details={"device_type": b})
            arg = self._special_arg(a, b)
            return Decoded(
                f"{cmd.name}{arg}", target="special", command=cmd.name,
                expects_answer=cmd.answer, send_twice=cmd.twice,
            )
        if addr.kind == AddrKind.RESERVED:
            return Decoded(f"reserved address byte 0x{a:02X} 0x{b:02X}")
        if not addr.is_command:
            if b == 0xFF:
                return Decoded(f"{addr}: DAPC MASK (stop fade)", target=str(addr), command="DAPC")
            return Decoded(f"{addr}: DAPC {b}", target=str(addr), command="DAPC", details={"level": b})
        if b in C.APPLICATION_EXTENDED_RANGE and dt is not None:
            table = C.DEVICE_TYPE_COMMANDS.get(dt, {})
            cmd = table.get(b)
            if cmd:
                return Decoded(
                    f"{addr}: {cmd.name} [DT{dt}]", target=str(addr), command=cmd.name,
                    expects_answer=cmd.answer, send_twice=cmd.twice,
                )
            return Decoded(f"{addr}: application extended 0x{b:02X} [DT{dt}]", target=str(addr))
        cmd = C.GEAR_COMMANDS.get(b)
        if cmd is None:
            if b in C.APPLICATION_EXTENDED_RANGE:
                return Decoded(f"{addr}: application extended 0x{b:02X} (no device type enabled)", target=str(addr))
            return Decoded(f"{addr}: reserved command 0x{b:02X}", target=str(addr))
        return Decoded(
            f"{addr}: {cmd.name_for(b)}", target=str(addr), command=cmd.name_for(b),
            expects_answer=cmd.answer, send_twice=cmd.twice,
        )

    @staticmethod
    def _special_arg(a: int, b: int) -> str:
        if a in (0xA5,):
            if b == 0x00:
                return " (all)"
            if b == 0xFF:
                return " (unaddressed)"
            if b & 1 and b < 0x80:
                return f" (A{b >> 1})"
            return f" 0x{b:02X} (none)"
        if a in (0xB7, 0xB9):
            if b == 0xFF:
                return " (delete)"
            return f" A{b >> 1}" if b & 1 and b < 0x80 else f" 0x{b:02X}"
        if a in (0xA1, 0xA7, 0xA9, 0xAB, 0xAD, 0xBB):
            return "" if b == 0 else f" 0x{b:02X}"
        return f" {b} (0x{b:02X})"

    # ------------------------------------------------------------------
    def _decode24(self, frame: Frame) -> Decoded:
        if not frame.bit(16):
            ev = Event.decode(frame)
            if ev is None:
                return Decoded(f"reserved event frame {frame.hex()}", is_event=True)
            t = ev.instance_type
            return Decoded(
                f"EVENT from {ev.source()}: {describe_event_info(t, ev.info)}",
                is_event=True, target=ev.source(), command="EVENT",
                details={"scheme": ev.scheme.name, "info": ev.info},
            )
        a, i, o = frame.byte(0), frame.byte(1), frame.byte(2)
        addr = DeviceAddress.decode(a)
        if addr.kind == AddrKind.SPECIAL:
            if a == 0xC1:
                cmd = C.DEVICE_SPECIAL.get(i)
                if cmd is None:
                    return Decoded(f"reserved device special 0x{i:02X} 0x{o:02X}")
                return Decoded(
                    f"{cmd.name} {o} (0x{o:02X})", target="special", command=cmd.name,
                    expects_answer=cmd.answer, send_twice=cmd.twice,
                )
            cmd = C.DEVICE_SPECIAL_2BYTE.get(a)
            if cmd:
                return Decoded(f"{cmd.name} 0x{i:02X} 0x{o:02X}", target="special", command=cmd.name,
                               expects_answer=cmd.answer)
            return Decoded(f"reserved device special 0x{a:02X}")
        if addr.kind == AddrKind.RESERVED:
            return Decoded(f"reserved 24-bit address 0x{a:02X}")
        inst = InstanceByte.decode(i)
        if inst.kind == InstKind.DEVICE:
            cmd = C.DEVICE_COMMANDS.get(o)
        else:
            cmd = C.INSTANCE_COMMANDS.get(o)
            if cmd is None and inst.kind == InstKind.TYPE:
                cmd = C.INSTANCE_TYPE_COMMANDS.get(inst.value or 0, {}).get(o)
        name = cmd.name if cmd else f"opcode 0x{o:02X}"
        return Decoded(
            f"{addr} {inst}: {name}", target=f"{addr} {inst}", command=name,
            expects_answer=bool(cmd and cmd.answer), send_twice=bool(cmd and cmd.twice),
        )


def decode(frame: Frame) -> str:
    """Stateless convenience wrapper."""
    return FrameDecoder().decode(frame).text
