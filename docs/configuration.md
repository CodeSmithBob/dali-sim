# Configuration (YAML)

A configuration describes one bus: its units, rooms and rules. See `examples/` for complete files.

```yaml
name: My test bus
description: Free text shown in the UI.
seed: 42               # makes random addresses and timings reproducible
speed: 1.0             # simulation speed (real-time clock)
commissioned: true     # false: ignore short addresses (units come "from the factory")
initial_level: 0       # optional: start all lamps at this level

bus:
  faults:              # fault injection, probabilities 0..1
    drop_forward: 0.0
    drop_backward: 0.0
    corrupt_backward: 0.0

world:
  outdoor_lux: 10000   # daylight outside
  people: false        # random people walking in and out
  daylight_cycle: false  # 24 h daylight curve on simulation time

rooms:
  - {name: Room 1, x: 0, y: 0, w: 4, h: 3, daylight_factor: 0.03, lux_per_lamp: 150}

gear:
  - name: Desk lamp
    type: led                # see the table below
    short_address: 0
    groups: [0, 15]
    scenes: {1: 120, 2: 254}
    fade_time: 3
    room: Room 1
    power_w: 30
  - name: "Panel {n}"        # {n} = 1, 2, 3...; {n:02d} works too
    count: 8
    start_address: 1         # consecutive short addresses
    type: tunable-white
    groups: [1]

devices:
  - name: Wall switch
    short_address: 0
    room: Room 1
    event_scheme: 2          # 0 instance, 1 device, 2 device/instance, 3 device group, 4 instance group
    groups: [0]              # device groups
    instances:
      - {type: push_button, label: On/Off}
      - {type: push_button, double_timer: 20}
  - name: Multi-sensor
    short_address: 1
    instances:
      - {type: occupancy, hold_s: 300, report_s: 20}
      - {type: light_sensor, max_lux: 2000, report_s: 30}

automation:                  # an application controller on the bus
  short_address: 63
  rules:
    - when: {short_address: 0, instance: 0, event: short_press}
      do: {toggle: G0}
  daylight:
    - {sensor: {short_address: 1, instance: 1}, target: G0, setpoint_lux: 500}
```

## Gear types

| `type` | Model |
|---|---|
| `led` (default) | DT6 LED driver |
| `plain` | gear without a device type (fluorescent-like) |
| `relay` | on/off: physical minimum 254 |
| `tunable-white` | DT6 + DT8 colour temperature (`coolest_k`, `warmest_k`, `auto_activation`) |
| `rgb`, `rgbw`, `rgbwaf` | DT6 + DT8 RGBWAF (`channels` for `rgbwaf`) |
| `xy` | DT6 + DT8 xy coordinates |
| `emergency` | DT1 (`rated_duration_min`, `emergency_level`) |

Common keys: `physical_min_level`, `power_w`, `dali_version` (1 or 2), `fade_time`, `fade_rate`, `min_level`, `max_level`, `power_on_level`, `system_failure_level`, `level`.

## Instance types

| `type` | Part | Keys |
|---|---|---|
| `push_button` | 301 | `double_timer` (× 20 ms, 0 = off) |
| `slider` / `absolute_input` | 302 | `resolution`, `switch` |
| `occupancy` | 303 | `hold_s`, `report_s` |
| `light_sensor` | 304 | `max_lux`, `resolution`, `report_s` |

All instances accept `event_scheme`, `priority` (2..5), `group` (primary instance group) and `label`.

## Rules

`when` matches fields of the **event frame**: `short_address`, `instance`, `instance_type`, `device_group`, `instance_group`, and `event`. That means a rule can only use what the configured event scheme puts in the frame, just like on a real bus.

Events: `short_press`, `double_press`, `long_press_start`, `long_press_repeat`, `long_press_stop`, `pressed`, `released`, `stuck`, `free`, `occupied`, `vacant`, `position`, `illuminance`, `any`. Add `repeat: true` to also match repeated occupancy reports.

Actions (`do`), where the target is `A5`, `G2` or `BC`:

- `toggle`, `off`, `recall_max`, `recall_min`, `up`, `down`, `on_and_step_up`, `step_down_and_off`, `last_active` take a target.
- `scene: {target, scene}` and `dapc: {target, level}`.
- `dim_by_position` sets the target's level from a slider position.
- `daylight` toggles a daylight loop on that target.

The YAML words `on` and `off` are read as booleans by YAML 1.1 parsers. The rule engine accepts them anyway: `off:` is OFF and `on:` is RECALL MAX.

## Several DALI lines

One DALI line holds at most 64 control gear and 64 control devices. A master or gateway with several DALI outputs drives several lines. Describe them under `lines:`. Each entry is a full line configuration, or a `file:` that takes a line from another file, plus overrides:

```yaml
name: Three-line building
seed: 3            # each line gets its own seed derived from this one
speed: 1.0         # shared by all lines
lines:
  - {file: building-64.yaml, name: Ground floor}
  - {file: building-64.yaml, name: First floor}
  - name: Second floor
    gear:
      - {name: "L{n:02d}", count: 64, start_address: 0, type: led}
```

- `file:` paths are relative to the building file.
- A line inherits `speed`, `commissioned`, `initial_level`, `world` and `bus` from the top level unless it sets them.
- The lines are separate buses: a broadcast on line 1 never reaches line 2.
- All lines run in one simulator process: one web page (pick the line at the top) and one Docker container.

Each line gets its own connections. Line N uses the base port + N − 1:

| Connection | Line 1 | Line 2 | Line 3 |
|---|---|---|---|
| TCP API (`--tcp-port 5555`) | 5555 | 5556 | 5557 |
| daliserver (`--daliserver-port 55825`) | 55825 | 55826 | 55827 |
| virtual serial port (`--pty /tmp/dali`) | `/tmp/dali` | `/tmp/dali2` | `/tmp/dali3` |
| fake USB stick (`--usb-stick tridonic,tridonic,tridonic`) | serial 0DA11001 | serial 0DA11002 | serial 0DA11003 |
| hardware bridge (`--adapter tridonic@2`) | `tridonic` or `tridonic@1` | `tridonic@2` | `tridonic@3` |

The TCP API also accepts a `"line": N` field on any port. CLI commands such as `dali-sim test` and `dali-sim commission` take `--line N`.

If you load a scenario with fewer lines from the web page, the missing lines stay as empty lines, so the ports and fake sticks keep working.
