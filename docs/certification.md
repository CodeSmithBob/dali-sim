# Pre-compliance testing and certification

## What official certification is

DALI-2 and D4i certification is a programme of the **DALI Alliance**. In short:

- The manufacturer must be a DALI Alliance member.
- The product is tested with the Alliance's **test sequences** for every IEC 62386 part it claims (102, 103, 207, 209, 301...). Depending on the programme, this is done by the manufacturer with the official test tools or by an approved test house.
- The results are submitted to the Alliance. After review, the product is listed in the public product database and may carry the DALI-2 or D4i logo.

The test sequences and test tools are available to members only. **dali-sim does not contain them and cannot certify anything.** Check the DALI Alliance website for the current rules, fees and test houses.

## What dali-sim offers

### 1. The pre-compliance test bench

```bash
dali-sim test office-16 --suite all                   # virtual units
dali-sim test --adapter hasseb --suite gear --addresses 0-3   # real units through an interface
dali-sim test --adapter hasseb --suite all --junit report.xml
```

It also runs from the **Test bench** tab of the web UI. The checks:

| Suite | Checks |
|---|---|
| `bus` | every short address is used once (duplicate random addresses), no gear without an address, unit count |
| `gear` | answers to QUERY CONTROL GEAR PRESENT; reset values after RESET; DTR0/1/2 round trips; a configuration command sent once is ignored, sent twice with a >100 ms gap is ignored, and sent twice correctly is executed; DAPC above max is limited and sets limit error; OFF, RECALL MIN, ON AND STEP UP; groups add/remove and group addressing; scenes store and recall; fade running bit and fade end level; memory bank 0 readable with a sane layout and version; device type answer |
| `devices` | QUERY DEVICE STATUS; RESET and reset state; DTR round trips with 24-bit commands; number and types of instances; SET EVENT SCHEME; DISABLE/ENABLE INSTANCE; quiescent mode start and stop |

Each unit is RESET at the end of its checks.

The checks follow the behaviour described in public DALI literature (see [sources.md](sources.md)). They catch the mistakes that most often fail certification: send-twice handling, reset values, limits and the memory bank layout. They are not a substitute for the official sequences.

### 2. Installations at scale

Use `building-64.yaml` or `commissioning-64.yaml`, or write your own configuration, to test a controller against 16, 32 or 64 gear and up to 64 control devices. Check that:

- commissioning finds every unit (`dali-sim commission` or the Commissioning tab);
- your controller copes with collisions, framing errors on broadcast queries, and lost frames (`bus.faults` in the YAML, or the `faults` action);
- event storms from many sensors (`people: true`) do not overload it.

### 3. Comparing a real device with the model

Run the same suite against the virtual gear and against your device through an interface, then compare the reports. Differences are either bugs in your device or in the simulator. Please report the second kind with the "Device behaves differently" issue template.
