# APIs

The server binds to `127.0.0.1` by default. Pass `--host 0.0.0.0` to expose it (Docker does this). The APIs have **no authentication**. Only expose them on networks you trust, especially when a bridge connects them to real lights.

## TCP API (JSON lines), port 5555

One JSON object per line in each direction. A request may carry an `id`; the response repeats it.

| Request | Response |
|---|---|
| `{"op":"hello"}` | `{"ok":true,"server":"dali-sim","version":"0.1.0","protocol":1,"name":"..."}` |
| `{"op":"send","frames":["FF91"]}` | `{"ok":true,"status":"answer","value":255,"responders":["..."]}` |
| `{"op":"send","frames":["A304","012E","012E"]}` | a transaction: DTR0 = 4, then SET FADE TIME twice; the reply is for the last frame |
| `{"op":"send","frames":["C10100","C10100"],"expect_answer":false,"priority":2}` | 24-bit frames are 6 hex digits |
| `{"op":"subscribe"}` | then `{"event":"record",...}` lines for every bus monitor record |
| `{"op":"snapshot"}` | full state of all units |
| `{"op":"act","action":"press","device":"Wall switch","instance":0}` | physical-world actions, see below |

`status` is `answer`, `none` or `framing_error`. Errors: `{"ok":false,"error":"..."}`.

Example in Python without dali-sim installed:

```python
import json, socket
s = socket.create_connection(("localhost", 5555))
f = s.makefile("rw")
f.write(json.dumps({"id": 1, "op": "send", "frames": ["FFA0"]}) + "\n"); f.flush()
print(json.loads(f.readline()))
```

Another dali-sim can use this API as an adapter: `--adapter remote:HOST:5555`.

## REST API

| Method and path | Body | Result |
|---|---|---|
| `GET /api/info` | | version and bus name |
| `GET /api/state` | | snapshot of all units, world and bus statistics |
| `GET /api/history?since=SEQ` | | bus monitor records |
| `GET /api/commands` | | command tables |
| `GET /api/examples` | | example names |
| `POST /api/load` | `{"example":"floor-32"}` | switch scenario |
| `POST /api/send` | `{"frames":["FF05"],"via":"virtual"}` | send raw frames |
| `POST /api/command` | `{"kind":"gear","target":"A3","opcode":46,"dtr0":4}` | builds DTR/ENABLE DEVICE TYPE/send-twice frames for you |
| `POST /api/act` | `{"action":"click","device":"...","instance":0}` | physical-world action |
| `POST /api/commission` | `{"devices":false,"only_new":true,"via":"virtual"}` | starts commissioning; progress arrives on the WebSocket |
| `POST /api/scan` | `{"via":"virtual"}` | starts a scan |
| `POST /api/testbench` | `{"suite":"gear","addresses":[0,1]}` | starts the test bench |
| `POST /api/job/cancel` | | cancels the running job |

`via` is `virtual` or the name of a hardware adapter with a running bridge.

`/api/command` kinds: `gear` (with optional `dt` for device type commands and `dapc: true` for a level), `gear_special` (`data`), `device` (`instance`: `device`, `all` or a number), `device_special` (`data`). Configuration commands are sent twice automatically; override that with `"twice": false`.

### Actions

`press`, `release`, `click` (`hold` seconds), `motion` (`moving`), `set_lux` (`lux`), `set_value` (`value` or `fraction`), `outdoor_lux` (`lux`), `people` (`enabled`), `mains` (`unit`, `on`), `bus_power` (`on`), `lamp_failure` / `gear_failure` (`unit`, `on`), `speed` (`value`), `faults` (`drop_forward`, `drop_backward`, `corrupt_backward`), `clear_addresses`.

## WebSocket `/ws`

The server sends:

- `{"type":"hello","history":[...],"state":{...}}` once;
- `{"type":"state","state":{...},"records":[...]}` about 8 times per second;
- `{"type":"progress",...}` during commissioning, scans and the test bench;
- `{"type":"job","name":...,"done":true,"result":...}` when a job ends.

The client may send `{"type":"act","action":"press",...}` for low-latency actions.
