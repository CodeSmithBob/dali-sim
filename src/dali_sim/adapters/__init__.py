"""Hardware adapters and the adapter registry.

An adapter is chosen with a spec string, ``name[:argument]``:

==========================  =================================================
``loopback``                a second simulated bus (testing, demos)
``remote:HOST:PORT``        another dali-sim over its TCP API
``hasseb[:PATH]``           Hasseb DALI Master (USB HID)
``tridonic[:PATH]``         Tridonic DALI USB (USB HID 17B5:0020)
``daliserver:HOST:PORT``    a daliserver (TCP 55825)
``lunatone:/dev/ttyX``      Lunatone LUBA interfaces (RS232, USB virtual COM port)
``lunatone:/dev/hidrawN``   older Lunatone DALI USB (same HID protocol as Tridonic)
``atxled:/dev/ttyX``        AtxLED DALI HAT for Raspberry Pi (serial)
``serial:/dev/ttyX``        any interface speaking the dali-sim serial line
                            protocol (DIY firmware, see docs/serial-protocol.md)
``gpio:tx=17,rx=27[,host=H]`` bit-banged DALI PHY on Raspberry Pi GPIO (pigpio
                            daemon on this machine, or on host H)
==========================  =================================================
"""

from __future__ import annotations

import importlib
from typing import Any

from .base import Adapter, AdapterError, Capabilities, Observed

_REGISTRY = {
    "loopback": ("dali_sim.adapters.simulated", "LoopbackAdapter"),
    "remote": ("dali_sim.adapters.simulated", "RemoteAdapter"),
    "hasseb": ("dali_sim.adapters.hasseb", "HassebAdapter"),
    "tridonic": ("dali_sim.adapters.tridonic", "TridonicAdapter"),
    "lunatone": ("dali_sim.adapters.lunatone", "LunatoneAdapter"),
    "atxled": ("dali_sim.adapters.atxled", "AtxLedAdapter"),
    "daliserver": ("dali_sim.adapters.daliserver", "DaliServerAdapter"),
    "serial": ("dali_sim.adapters.serial_line", "SerialLineAdapter"),
    "gpio": ("dali_sim.adapters.gpio", "GpioAdapter"),
}


def adapter_names() -> list[str]:
    return list(_REGISTRY)


def adapter_class(name: str) -> type[Adapter]:
    try:
        module, cls = _REGISTRY[name]
    except KeyError:
        raise AdapterError(f"unknown adapter {name!r}; known: {', '.join(_REGISTRY)}") from None
    return getattr(importlib.import_module(module), cls)


def create(spec: str, **kw: Any) -> Adapter:
    """Create an adapter from a spec string like ``hasseb`` or ``serial:/dev/ttyUSB0``."""
    name, _, arg = spec.partition(":")
    cls = adapter_class(name)
    if name in ("remote", "daliserver"):
        host, _, port = arg.rpartition(":")
        if not port.isdigit():
            host, port = arg, ""
        return cls(host=host or "127.0.0.1", port=int(port or (5555 if name == "remote" else 55825)), **kw)
    if name == "lunatone" and (arg.startswith("/dev/hidraw") or arg in ("hid", "usb-hid")):
        from .tridonic import TridonicAdapter

        a = TridonicAdapter(path=arg if arg.startswith("/dev/") else None, **kw)
        a.name = "lunatone"
        return a
    if name == "gpio":
        opts = dict(p.split("=", 1) for p in arg.split(",") if "=" in p)
        return cls(**{k: int(v) if v.isdigit() else v for k, v in opts.items()}, **kw)
    if arg:
        kw["path"] = arg
    return cls(**kw)


__all__ = ["Adapter", "AdapterError", "Capabilities", "Observed", "adapter_class", "adapter_names", "create"]
