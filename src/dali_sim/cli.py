"""Command line interface: ``dali-sim <command> ...``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .protocol.decode import FrameDecoder

EXAMPLES = Path(__file__).resolve().parent.parent.parent / "examples"


def _examples_dir() -> Path | None:
    for p in (Path.cwd() / "examples", EXAMPLES, Path("/app/examples")):
        if p.is_dir():
            return p
    return None


def _resolve_config(name: str | None) -> Path | None:
    if not name:
        return None
    p = Path(name)
    if p.is_file():
        return p
    ex = _examples_dir()
    if ex and (ex / f"{name}.yaml").is_file():
        return ex / f"{name}.yaml"
    raise SystemExit(f"config not found: {name}")


def _load(name: str | None, manual: bool = False, speed: float | None = None):
    from .config import build, load

    path = _resolve_config(name)
    sim = load(path, manual_clock=manual) if path else build({"name": "Empty bus"}, manual_clock=manual)
    if speed is not None and not manual:
        sim.speed = speed
    return sim


# ---------------------------------------------------------------------------
async def cmd_serve(args: argparse.Namespace) -> None:
    from aiohttp import web

    from .bridge import Bridge
    from .config import load
    from .server.tcp import TcpServer
    from .server.web import WebApp

    sim = _load(args.config, speed=args.speed)
    for spec in args.adapter or []:
        from .adapters import create

        sim.bridges.append(Bridge(sim, create(spec), mode=args.bridge_mode))
    tcp = None
    if args.tcp_port:
        tcp = TcpServer(sim, args.host, args.tcp_port)

    async def start_pty() -> None:
        if args.pty:
            from .server.pty import PtyServer

            path = await PtyServer(sim, args.pty, protocol=args.pty_protocol).start()
            print(f"  serial   {path} ({args.pty_protocol} protocol)")
        if args.daliserver_port:
            from .server.daliserver import DaliServerProtocolServer

            ds = DaliServerProtocolServer(sim, args.host, args.daliserver_port)
            await ds.start()
            print(f"  daliserv {args.host}:{ds.port} (daliserver protocol)")
        for kind in args.usb_stick or []:
            from .server.uhid import FakeUsbStick

            stick = FakeUsbStick(sim, kind)
            try:
                await stick.start()
            except RuntimeError as exc:
                print(f"  usb      fake {kind} stick NOT started: {exc}")
                continue
            await asyncio.sleep(0.3)  # let the kernel create the hidraw node
            nodes = ", ".join(stick.hidraw_paths()) or "see /dev/hidraw*"
            print(f"  usb      fake {kind} stick {stick.model.vid:04x}:{stick.model.pid:04x} at {nodes}")

    if args.no_web:
        await sim.start()
        if tcp:
            await tcp.start()
            print(f"TCP API on {args.host}:{tcp.port}")
        await start_pty()
        print(f"Simulating {sim.name!r}: {len(sim.gear)} gear, {len(sim.devices)} devices. Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        finally:
            await sim.stop()
        return
    webapp = WebApp(sim, examples_dir=_examples_dir(), loader=lambda p: load(p))
    runner = web.AppRunner(webapp.app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()
    if tcp:
        # The TCP server must follow scenario changes made in the web UI.
        tcp.sim = webapp.sim
        await tcp.start()

        async def follow() -> None:
            while True:
                tcp.sim = webapp.sim
                await asyncio.sleep(0.5)

        asyncio.create_task(follow())
    shown = "localhost" if args.host in ("0.0.0.0", "::") else args.host
    print(f"dali-sim {__version__}: {sim.name!r} with {len(sim.gear)} gear and {len(sim.devices)} devices")
    print(f"  web UI   http://{shown}:{args.port}/")
    if tcp:
        print(f"  TCP API  {shown}:{tcp.port} (JSON lines)")
    for br in sim.bridges:
        print(f"  bridge   {br.adapter.name} ({br.mode})" + (f"  ERROR: {br.error}" if br.error else ""))
    await start_pty()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


async def cmd_commission(args: argparse.Namespace) -> None:
    from .controller import Controller

    transport, close = await _transport(args)
    try:
        c = Controller(transport)
        for devices in ([False, True] if args.what == "all" else [args.what == "devices"]):
            found = await c.commission(devices=devices, only_new=not args.readdress)
            label = "control devices" if devices else "control gear"
            print(f"{label}: {len(found)} addressed")
            for f in found:
                print(f"  {'D' if devices else 'A'}{f.short_address:<3} random {f.random_address:06X}")
        if hasattr(transport, "now"):
            print(f"bus time used: {transport.now():.1f} s")
    finally:
        await close()


async def cmd_scan(args: argparse.Namespace) -> None:
    from .controller import Controller

    transport, close = await _transport(args)
    try:
        res = await Controller(transport).scan(details=not args.quick)
        print(json.dumps({"gear": res.gear, "devices": res.devices}, indent=1, default=str))
    finally:
        await close()


async def cmd_send(args: argparse.Namespace) -> None:
    from .server.tcp import parse_frame

    transport, close = await _transport(args)
    try:
        frames = [parse_frame(f) for f in args.frames]
        if args.twice:
            frames = frames + frames[-1:]
        dec = FrameDecoder()
        for f in frames:
            print(f"> {f.hex():<9} {dec.decode(f).text}")
        r = await transport.send(frames, source="cli")
        if r.answered:
            print(f"< {r.value:02X}        answer {r.value} (0b{r.value:08b})")
        else:
            print(f"< {r.status}")
    finally:
        await close()


async def cmd_sniff(args: argparse.Namespace) -> None:
    from .adapters import create

    adapter = create(args.adapter)
    dec = FrameDecoder()

    def show(obs) -> None:
        if obs.frame is not None:
            print(f"{obs.t:12.4f} {obs.kind:<13} {obs.frame.hex():<9} {dec.decode(obs.frame).text}", flush=True)
        else:
            print(f"{obs.t:12.4f} {obs.kind}", flush=True)

    adapter.listeners.append(show)
    await adapter.open()
    if not adapter.caps.sniff:
        print(f"note: {adapter.name} cannot report frames sent by others; only own traffic is shown", file=sys.stderr)
    try:
        await asyncio.Event().wait()
    finally:
        await adapter.close()


async def cmd_test(args: argparse.Namespace) -> None:
    from .testbench import run_suite

    transport, close = await _transport(args)
    try:
        addrs = None
        if args.addresses:
            addrs = []
            for part in args.addresses.split(","):
                a, _, b = part.partition("-")
                addrs += list(range(int(a), int(b or a) + 1))
        res = await run_suite(transport, suite=args.suite, addresses=addrs, fade_check=not args.no_fade)
    finally:
        await close()
    for r in res["results"]:
        if not r["passed"] or args.verbose:
            print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['target']:<5} {r['test']}  {r['detail']}")
    print(f"{res['passed']} passed, {res['failed']} failed")
    if args.junit:
        write_junit(res, Path(args.junit))
    if res["failed"]:
        raise SystemExit(1)


def write_junit(res: dict[str, Any], path: Path) -> None:
    from xml.sax.saxutils import escape

    cases = []
    for r in res["results"]:
        name = escape(f"{r['target']}: {r['test']}", {'"': "&quot;"})
        detail = escape(r["detail"], {'"': "&quot;"})
        body = "" if r["passed"] else f'<failure message="{detail}"/>'
        cases.append(f'  <testcase classname="dali-sim.{r["suite"]}" name="{name}">{body}</testcase>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="dali-sim {res["suite"]}" '
           f'tests="{len(res["results"])}" failures="{res["failed"]}">\n' + "\n".join(cases) + "\n</testsuite>\n")
    path.write_text(xml, encoding="utf-8")


async def _transport(args: argparse.Namespace):
    """The bus a command talks to: an adapter, a remote dali-sim, or a fast
    in-process simulation of the given config."""
    if getattr(args, "adapter", None):
        from .adapters import create

        adapter = create(args.adapter)
        await adapter.open()
        return adapter, adapter.close
    sim = _load(getattr(args, "config", None), manual=True)
    sim.world.start(sim)

    async def noop() -> None:
        return None

    return sim.bus, noop


def cmd_decode(args: argparse.Namespace) -> None:
    from .protocol.events import Event
    from .server.tcp import parse_frame

    dec = FrameDecoder()
    for text in args.frames:
        f = parse_frame(text)
        d = dec.decode(f)
        print(f"{f.hex():<9} {d.text}")
        if d.expects_answer:
            print("          expects an answer")
        if d.send_twice:
            print("          must be sent twice within 100 ms")
        ev = Event.decode(f)
        if ev:
            print(f"          event scheme {ev.scheme.name}, info {ev.info}")


def cmd_commands(args: argparse.Namespace) -> None:
    from .protocol import commands as C

    tables = {
        "Control gear (IEC 62386-102)": C.GEAR_COMMANDS,
        "Special commands (IEC 62386-102)": C.GEAR_SPECIAL,
        "DT1 emergency (IEC 62386-202)": C.DT1_COMMANDS,
        "DT6 LED (IEC 62386-207)": C.DT6_COMMANDS,
        "DT8 colour (IEC 62386-209)": C.DT8_COMMANDS,
        "Control device (IEC 62386-103)": C.DEVICE_COMMANDS,
        "Instance (IEC 62386-103)": C.INSTANCE_COMMANDS,
        "Device special commands (IEC 62386-103, address byte 0xC1)": C.DEVICE_SPECIAL,
    }
    for title, table in tables.items():
        print(f"\n## {title}\n\n| Opcode | Command | Answer | Twice |\n|---|---|---|---|")
        for op, cmd in sorted(table.items()):
            notes = " (DALI-2)" if cmd.dali2 else " (DALI-1 only)" if cmd.legacy else ""
            print(f"| 0x{op:02X} | {cmd.name_for(op)}{notes} | {'yes' if cmd.answer else ''} | {'yes' if cmd.twice else ''} |")


def cmd_adapters(args: argparse.Namespace) -> None:
    from .adapters import adapter_class, adapter_names

    for name in adapter_names():
        try:
            c = adapter_class(name).caps
            print(f"{name:<10} 24-bit:{'yes' if c.forward24 else 'no ':<3}  sniff:{'yes' if c.sniff else 'no ':<3}  "
                  f"answers:{'yes' if c.send_backward else 'no ':<3}  {c.notes}")
        except Exception as exc:  # optional dependency missing
            print(f"{name:<10} unavailable: {exc}")


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dali-sim", description="DALI / DALI-2 bus simulator")
    p.add_argument("--version", action="version", version=f"dali-sim {__version__}")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the simulator with the web UI and TCP API")
    s.add_argument("config", nargs="?", default="office-16", help="YAML file or example name (default office-16)")
    s.add_argument("--host", default="127.0.0.1", help="bind address (use 0.0.0.0 in Docker)")
    s.add_argument("--port", type=int, default=8080, help="web UI port")
    s.add_argument("--tcp-port", type=int, default=5555, help="JSON-lines TCP API port (0 = off)")
    s.add_argument("--speed", type=float, default=None, help="simulation speed factor")
    s.add_argument("--adapter", action="append", help="hardware adapter spec, e.g. hasseb or serial:/dev/ttyUSB0")
    s.add_argument("--bridge-mode", default="controller", choices=["controller", "devices", "hybrid"])
    s.add_argument("--no-web", action="store_true", help="no web UI, only the TCP API")
    s.add_argument("--pty", nargs="?", const="/tmp/dali-sim-tty", default=None, metavar="LINK",
                   help="also offer a virtual serial port, default link /tmp/dali-sim-tty")
    s.add_argument("--pty-protocol", default="dalisim", choices=["dalisim", "atxled", "luba"],
                   help="protocol on the virtual serial port: dali-sim line protocol, AtxLED HAT or Lunatone LUBA")
    s.add_argument("--usb-stick", action="append", choices=["tridonic", "lunatone", "hasseb"], metavar="KIND",
                   help="appear as a USB DALI stick (tridonic, lunatone or hasseb) via Linux /dev/uhid; repeatable")
    s.add_argument("--daliserver-port", type=int, default=0, metavar="PORT",
                   help="also serve the daliserver TCP protocol (usually 55825)")
    s.set_defaults(func=cmd_serve, is_async=True)

    for name, fn, hlp in (("commission", cmd_commission, "give short addresses (binary search)"),
                          ("scan", cmd_scan, "list the units on a bus"),
                          ("test", cmd_test, "run the pre-compliance test bench")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("config", nargs="?", help="YAML file or example name (virtual bus)")
        s.add_argument("--adapter", help="use real hardware instead of a virtual bus")
        if name == "commission":
            s.add_argument("--what", choices=["gear", "devices", "all"], default="all")
            s.add_argument("--readdress", action="store_true", help="also re-address units that have an address")
        if name == "scan":
            s.add_argument("--quick", action="store_true", help="presence only")
        if name == "test":
            s.add_argument("--suite", default="all", choices=["bus", "gear", "devices", "all"])
            s.add_argument("--addresses", help="e.g. 0-3,7")
            s.add_argument("--no-fade", action="store_true", help="skip the fade timing test")
            s.add_argument("--junit", help="write a JUnit XML report")
        s.set_defaults(func=fn, is_async=True)

    s = sub.add_parser("send", help="send frames (hex) and print the answer")
    s.add_argument("frames", nargs="+", help="e.g. FF90 or 'A3 05' '01 2E'")
    s.add_argument("--config", help="YAML file or example name (virtual bus)")
    s.add_argument("--adapter", help="hardware adapter or remote:HOST:PORT")
    s.add_argument("--twice", action="store_true", help="send the last frame twice")
    s.set_defaults(func=cmd_send, is_async=True)

    s = sub.add_parser("sniff", help="print the traffic seen by an adapter")
    s.add_argument("--adapter", required=True)
    s.set_defaults(func=cmd_sniff, is_async=True)

    s = sub.add_parser("decode", help="decode hex frames")
    s.add_argument("frames", nargs="+")
    s.set_defaults(func=cmd_decode, is_async=False)

    s = sub.add_parser("commands", help="print the command tables (markdown)")
    s.set_defaults(func=cmd_commands, is_async=False)

    s = sub.add_parser("adapters", help="list hardware adapters and what they can do")
    s.set_defaults(func=cmd_adapters, is_async=False)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    if args.is_async:
        try:
            asyncio.run(args.func(args))
        except KeyboardInterrupt:
            pass
    else:
        args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
