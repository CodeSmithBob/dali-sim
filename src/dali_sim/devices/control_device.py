"""A DALI-2 control device (IEC 62386-103): push buttons, sensors, and
application controllers.  Control devices use 24-bit frames, have up to 32
*instances* (one per input), and send *event messages* on the bus."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import MASK, YES, InitState, Unit
from ..memory import DALI2_VERSION, MemoryBank, make_bank0, make_bank1
from ..protocol import timing as T
from ..protocol.address import AddrKind, DeviceAddress, InstanceByte, InstKind
from ..protocol.commands import DEVICE_COMMANDS, DEVICE_SPECIAL, INSTANCE_COMMANDS
from ..protocol.events import Event, EventScheme
from ..protocol.frames import Frame

if TYPE_CHECKING:  # pragma: no cover
    from .instances import Instance


class ControlDevice(Unit):
    kind = "device"

    def __init__(self, name: str, *, instances: list[Instance] | None = None, short_address: int | None = None,
                 application_controller: bool = False, gtin: int = 0x0000_8765_4321, serial: int | None = None,
                 random_address: int | None = None, seed: int | None = None) -> None:
        super().__init__(name, short_address=short_address, random_address=random_address, seed=seed)
        self.instances: list[Instance] = []
        for inst in instances or []:
            self.add_instance(inst)
        self.device_groups = 0
        self.app_controller_present = application_controller
        self.app_controller_enabled = application_controller
        self.app_controller_error = False
        self.input_device_error = False
        self.operating_mode = 0
        self.quiescent_until = -1.0
        self.power_cycle_notification = False
        serial = self.rng.getrandbits(40) if serial is None else serial
        self.banks: dict[int, MemoryBank] = {
            0: make_bank0(gtin=gtin, firmware=(1, 0), serial=serial, hardware=(1, 0), v101=DALI2_VERSION,
                          v102=MASK, v103=DALI2_VERSION, n_devices=1, n_gear=0, index=0, last_bank=1),
            1: make_bank1(),
        }

    def add_instance(self, inst: Instance) -> Instance:
        inst.number = len(self.instances)
        inst.device = self
        self.instances.append(inst)
        return inst

    def bind(self, bus) -> None:  # type: ignore[override]
        super().bind(bus)
        for inst in self.instances:
            inst.bound()

    # -- state -------------------------------------------------------------
    @property
    def quiescent(self) -> bool:
        return self.bus is not None and self.bus.now() < self.quiescent_until

    def status_byte(self) -> int:
        bits = [
            self.input_device_error or any(i.error for i in self.instances),
            self.quiescent,
            self.short_address == MASK,
            self.app_controller_enabled and self.app_controller_present,
            self.app_controller_error,
            self.power_cycle_seen,
            self.reset_state(),
        ]
        return sum(int(b) << i for i, b in enumerate(bits))

    def reset_state(self) -> bool:
        return (self.device_groups == 0 and self.random_address == 0xFFFFFF and self.operating_mode == 0
                and not self.power_cycle_notification and all(i.reset_state() for i in self.instances))

    def _reset(self) -> None:
        self.device_groups = 0
        self.random_address = 0xFFFFFF
        self.search_address = 0xFFFFFF
        self.operating_mode = 0
        self.power_cycle_notification = False
        self.quiescent_until = -1.0
        self.app_controller_enabled = self.app_controller_present
        self.power_cycle_seen = False
        for i in self.instances:
            i.reset()

    def set_mains(self, on: bool) -> None:
        if on == self.mains:
            return
        self.mains = on
        if on:
            self.power_cycle_seen = True
            self.dtr0 = self.dtr1 = self.dtr2 = 0
        else:
            self.init_state = InitState.DISABLED

    def on_bus_power(self, powered: bool, t: float) -> None:
        if powered:
            self.power_cycle_seen = True

    # -- events ------------------------------------------------------------
    def emit(self, inst: Instance, info: int, note: str = "") -> bool:
        """Send an event for ``inst``.  Returns False if it was suppressed."""
        if self.bus is None or not self.online or self.quiescent or not inst.enabled:
            return False
        scheme = EventScheme(inst.event_scheme) if inst.event_scheme <= 4 else EventScheme.INSTANCE
        if scheme in (EventScheme.DEVICE, EventScheme.DEVICE_INSTANCE) and self.short_address == MASK:
            scheme = EventScheme.INSTANCE
        group = next((g for g in range(32) if self.device_groups & (1 << g)), None)
        if scheme == EventScheme.DEVICE_GROUP and group is None:
            scheme = EventScheme.INSTANCE
        if scheme == EventScheme.INSTANCE_GROUP and inst.primary_group == MASK:
            scheme = EventScheme.INSTANCE
        ev = Event(scheme, info & 0x3FF, short_address=self.short_address, device_group=group,
                   instance_type=inst.instance_type, instance_number=inst.number,
                   instance_group=inst.primary_group)
        self.bus.submit(ev.encode(), source=self.name, priority=inst.event_priority, multi_master=True,
                        expect_answer=False, note=note)
        return True

    # -- addressing ---------------------------------------------------------
    def addressed(self, addr: DeviceAddress) -> bool:
        if addr.kind == AddrKind.SHORT:
            return addr.value == self.short_address
        if addr.kind == AddrKind.GROUP:
            return bool(self.device_groups & (1 << addr.value))
        if addr.kind == AddrKind.BROADCAST:
            return True
        if addr.kind == AddrKind.BROADCAST_UNADDRESSED:
            return self.short_address == MASK
        return False

    def selected_instances(self, ib: InstanceByte) -> list[Instance]:
        k, v = ib.kind, ib.value
        if k == InstKind.NUMBER:
            return [i for i in self.instances if i.number == v]
        if k == InstKind.GROUP:
            return [i for i in self.instances if v in (i.primary_group, i.group1, i.group2)]
        if k == InstKind.TYPE:
            return [i for i in self.instances if i.instance_type == v]
        if k == InstKind.BROADCAST:
            return list(self.instances)
        return []

    # -- frame entry point ----------------------------------------------------
    def on_frame(self, frame: Frame, t: float, source: str) -> int | list[int] | None:
        if not frame.is_forward:
            return None
        self.twice.other_frame(frame)
        if frame.bits != 24 or not self.online or not frame.bit(16):
            return None  # 16-bit frames and event messages are not for us
        a, ib, op = frame.byte(0), frame.byte(1), frame.byte(2)
        if a == 0xC1:
            return self._special(ib, op, frame, t)
        if a in (0xC5, 0xC7, 0xC9):
            return self._special2(a, ib, op)
        addr = DeviceAddress.decode(a)
        if addr.kind in (AddrKind.SPECIAL, AddrKind.RESERVED) or not self.addressed(addr):
            return None
        inst_byte = InstanceByte.decode(ib)
        if inst_byte.kind == InstKind.DEVICE:
            cmd = DEVICE_COMMANDS.get(op)
            if cmd is None:
                return None
            if cmd.twice and not self.twice.confirm(frame, t):
                return None
            if op != 0x15 and not 0x36 <= op <= 0x38 and op != 0x3C:
                self.write_enabled = False
            return self._device_command(op, t)
        targets = self.selected_instances(inst_byte)
        if not targets:
            return None
        cmd = INSTANCE_COMMANDS.get(op)
        if cmd is None:
            cmd = targets[0].type_commands().get(op)
            if cmd is None:
                return None
        confirmed = False
        if cmd.twice:
            confirmed = self.twice.confirm(frame, t)
            if not confirmed:
                return None
        answers = []
        for inst in targets:
            if op in INSTANCE_COMMANDS:
                v = self._instance_command(inst, op)
            else:
                v = inst.type_command(op, t, confirmed)
            if v is not None:
                answers.append(v)
        if not answers:
            return None
        return answers[0] if len(answers) == 1 else answers

    # -- device commands -------------------------------------------------------
    def _device_command(self, op: int, t: float) -> int | None:  # noqa: C901
        if op == 0x00:
            self._identify(t)
        elif op == 0x01:
            self.power_cycle_seen = False
        elif op == 0x10:
            self._reset()
        elif op == 0x11:
            for n, bank in self.banks.items():
                if n and (self.dtr0 == 0 or self.dtr0 == n):
                    bank.reset()
        elif op == 0x14:  # SET SHORT ADDRESS
            if self.dtr0 == MASK:
                self.short_address = MASK
            elif self.dtr0 <= 63:
                self.short_address = self.dtr0
        elif op == 0x15:
            self.write_enabled = True
        elif op == 0x16:
            if self.app_controller_present:
                self.app_controller_enabled = True
        elif op == 0x17:
            self.app_controller_enabled = False
        elif op == 0x18:
            if self.dtr0 == 0:
                self.operating_mode = 0
        elif op in (0x19, 0x1A, 0x1B, 0x1C):
            mask = (self.dtr2 << 8) | self.dtr1
            if op in (0x1A, 0x1C):
                mask <<= 16
            if op in (0x19, 0x1A):
                self.device_groups |= mask
            else:
                self.device_groups &= ~mask
        elif op == 0x1D:
            self.quiescent_until = t + T.QUIESCENT_MODE_DURATION
        elif op == 0x1E:
            self.quiescent_until = -1.0
        elif op == 0x1F:
            self.power_cycle_notification = True
        elif op == 0x20:
            self.power_cycle_notification = False
        elif op == 0x21:
            pass
        elif op == 0x30:
            return self.status_byte()
        elif op == 0x31:
            return YES if self.app_controller_error else None
        elif op == 0x32:
            return YES if self.input_device_error else None
        elif op == 0x33:
            return YES if self.short_address == MASK else None
        elif op == 0x34:
            return self.banks[0].data[0x17]
        elif op == 0x35:
            return len(self.instances)
        elif op == 0x36:
            return self.dtr0
        elif op == 0x37:
            return self.dtr1
        elif op == 0x38:
            return self.dtr2
        elif op == 0x39:
            return (self.random_address >> 16) & 0xFF
        elif op == 0x3A:
            return (self.random_address >> 8) & 0xFF
        elif op == 0x3B:
            return self.random_address & 0xFF
        elif op == 0x3C:
            bank = self.banks.get(self.dtr1)
            v = bank.read(self.dtr0) if bank else None
            if v is not None and self.dtr0 < 0xFF:
                self.dtr0 += 1
            return v
        elif op == 0x3D:
            return YES if self.app_controller_enabled else None
        elif op == 0x3E:
            return self.operating_mode
        elif op == 0x3F:
            return YES if self.operating_mode >= 0x80 else None
        elif op == 0x40:
            return YES if self.quiescent else None
        elif 0x41 <= op <= 0x44:
            return (self.device_groups >> (8 * (op - 0x41))) & 0xFF
        elif op == 0x45:
            return YES if self.power_cycle_notification else None
        elif op == 0x46:
            return (int(self.app_controller_present) | (int(bool(self.instances)) << 1))
        elif op == 0x47:
            return MASK
        elif op == 0x48:
            return YES if self.reset_state() else None
        elif op == 0x49:
            return None
        return None

    def _instance_command(self, inst: Instance, op: int) -> int | None:  # noqa: C901
        if op == 0x61:
            if 2 <= self.dtr0 <= 5:
                inst.event_priority = self.dtr0
        elif op == 0x62:
            inst.enabled = True
        elif op == 0x63:
            inst.enabled = False
        elif op in (0x64, 0x65, 0x66):
            if self.dtr0 <= 31 or self.dtr0 == MASK:
                setattr(inst, {0x64: "primary_group", 0x65: "group1", 0x66: "group2"}[op], self.dtr0)
        elif op == 0x67:
            if self.dtr0 <= 4:
                inst.event_scheme = self.dtr0
        elif op == 0x68:
            inst.event_filter = (self.dtr0 | (self.dtr1 << 8) | (self.dtr2 << 16)) & inst.filter_mask
        elif op == 0x80:
            return inst.instance_type
        elif op == 0x81:
            return inst.resolution
        elif op == 0x82:
            return inst.error_code if inst.error else None
        elif op == 0x83:
            return int(inst.error) | (int(inst.active) << 1)
        elif op == 0x84:
            return inst.event_priority
        elif op == 0x86:
            return YES if inst.enabled else None
        elif op == 0x88:
            return inst.primary_group
        elif op == 0x89:
            return inst.group1
        elif op == 0x8A:
            return inst.group2
        elif op == 0x8B:
            return inst.event_scheme
        elif op == 0x8C:
            return inst.query_input_value()
        elif op == 0x8D:
            return inst.query_input_latch()
        elif op == 0x8E:
            return 0xFE  # no features
        elif op == 0x8F:
            return None
        elif 0x90 <= op <= 0x92:
            return (inst.event_filter >> (8 * (op - 0x90))) & 0xFF
        return None

    # -- special commands -------------------------------------------------------
    def _special(self, op: int, data: int, frame: Frame, t: float) -> int | None:  # noqa: C901
        cmd = DEVICE_SPECIAL.get(op)
        if cmd is None:
            return None
        if cmd.twice and not self.twice.confirm(frame, t):
            return None
        init_active = self.init_state != InitState.DISABLED
        if op == 0x00:
            self._terminate()
        elif op == 0x01:  # INITIALISE: 0..63 address, 0x7F unaddressed, 0xFF all
            if (data == 0xFF or (data == 0x7F and self.short_address == MASK)
                    or (data <= 63 and data == self.short_address)):
                self._start_initialise(t)
        elif op == 0x02:
            if init_active:
                self._randomise()
        elif op == 0x03:
            if self.init_state == InitState.ENABLED and self.random_address <= self.search_address:
                return YES
        elif op == 0x04:
            if self.init_state == InitState.ENABLED and self.random_address == self.search_address:
                self.init_state = InitState.WITHDRAWN
        elif op in (0x05, 0x06, 0x07):
            if init_active:
                self._set_search_byte(op - 0x05, data)
        elif op == 0x08:
            if init_active and self.random_address == self.search_address:
                if data == MASK:
                    self.short_address = MASK
                elif data <= 63:
                    self.short_address = data
        elif op == 0x09:
            if init_active and data == self.short_address:
                return YES
        elif op == 0x0A:
            if init_active and self.random_address == self.search_address:
                return self.short_address
        elif op in (0x20, 0x21):
            return self._write_memory(data, reply=op == 0x20)
        elif op == 0x30:
            self.dtr0 = data
        elif op == 0x31:
            self.dtr1 = data
        elif op == 0x32:
            self.dtr2 = data
        return None

    def _special2(self, a: int, b1: int, b2: int) -> int | None:
        if a == 0xC7:
            self.dtr1, self.dtr0 = b1, b2
        elif a == 0xC9:
            self.dtr2, self.dtr1 = b1, b2
        elif a == 0xC5:  # DIRECT WRITE MEMORY: offset, data (bank in DTR1)
            self.dtr0 = b1
            return self._write_memory(b2, reply=True)
        return None

    def _write_memory(self, value: int, reply: bool) -> int | None:
        if not self.write_enabled:
            return None
        bank = self.banks.get(self.dtr1)
        if bank is None:
            return None
        loc = self.dtr0
        ok = bank.write(loc, value)
        if loc < 0xFF:
            self.dtr0 = loc + 1
        return value if ok and reply else None

    # -- visualisation ---------------------------------------------------------
    def snapshot(self, t: float) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": "device",
            "short_address": None if self.short_address == MASK else self.short_address,
            "random_address": f"{self.random_address:06X}",
            "groups": [g for g in range(32) if self.device_groups & (1 << g)],
            "status": self.status_byte(),
            "mains": self.mains,
            "quiescent": self.quiescent,
            "identifying": self.identify_until > t,
            "init_state": self.init_state.value,
            "application_controller": self.app_controller_present,
            "instances": [i.snapshot(t) for i in self.instances],
            "meta": self.meta,
        }
