# Contributing to dali-sim

Thank you for helping. Device types, adapters, firmware, tests, documentation and UI work are all welcome.

## Ground rules

1. **No text from IEC 62386 or other paid standards.** Do not paste clauses, tables or figures from the standard, from leaked copies, or from documents whose licence does not allow it. Describe behaviour in your own words and cite the part and clause (e.g. "IEC 62386-102:2014, 9.7"). Facts such as opcodes and bit layouts are fine.
2. **Cite a public source** for new behaviour in `docs/sources.md`. If you only have the standard, say so. If a value is unverified, add it to the "To verify" list.
3. **Respect other licences.** dali-sim is Apache-2.0. Do not copy code from GPL/LGPL projects (for example python-dali, which is LGPL-3.0). Reading them to understand a protocol and then writing your own implementation is fine; credit them in `docs/sources.md`.
4. **Tests come with changes.** Every behaviour change needs a test in `tests/`.

## Development setup

```bash
git clone https://github.com/CodeSmithBob/dali-sim && cd dali-sim
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,hardware]"
pytest
ruff check src tests
dali-sim serve office-16   # http://localhost:8080
```

## Where things live

See [docs/architecture.md](docs/architecture.md). In short:

- `src/dali_sim/protocol/`: frames, timing, command tables (no state)
- `src/dali_sim/gear/`, `src/dali_sim/devices/`: unit models
- `src/dali_sim/adapters/`: hardware interfaces
- `src/dali_sim/server/`: web UI, REST, WebSocket, TCP, pty
- `examples/`: YAML scenarios

## Good first contributions

- A device type that is not modelled yet (DT7 switching, DT17 dimming curve selection, D4i DT49..52).
- An instance type (305 colour sensor) or input-device features (feature instances).
- Firmware for the [serial line protocol](docs/serial-protocol.md) on an RP2040 or ESP32.
- A hardware report: run `dali-sim scan --adapter ...` and `dali-sim test --adapter ...` with your interface and real devices, and open a "Hardware report" issue.
- Checking a value in the "To verify" list of `docs/sources.md` against a public source.

## Pull requests

- Keep them focused; describe what you changed and how you tested it.
- Run `pytest` and `ruff check src tests` before pushing; CI runs both on Python 3.10 to 3.13.
- New user-visible behaviour also needs docs (README, `docs/`) and, if it is configurable, an example.

## Reporting bugs and device differences

Use the issue templates. For "the simulator behaves differently from my real device", include the frames (bus monitor CSV export), what the real device answered, and its make, model and firmware.
