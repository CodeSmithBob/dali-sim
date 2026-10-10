# Docker

```bash
docker build -t dali-sim .
docker run --rm -p 8080:8080 -p 5555:5555 dali-sim                 # office-16
docker run --rm -p 8080:8080 dali-sim serve building-64 --host 0.0.0.0
docker run --rm -v $PWD/my-bus.yaml:/bus.yaml -p 8080:8080 dali-sim serve /bus.yaml --host 0.0.0.0
```

If Docker Hub rate-limits you, build from a mirror: `docker build --build-arg BASE=mirror.gcr.io/library/python:3.12-slim -t dali-sim .`

## Compose

`docker compose up` starts the simulator. `docker compose --profile demo up` also starts a second container that runs the test bench against the simulator over the TCP API (`--adapter remote:dali-sim:5555`). That is the pattern for integrating your own controller container: point it at `dali-sim:5555`.

## Real adapters in a container (Linux)

Pass the device node in and pick the adapter:

```bash
# USB HID interfaces (Hasseb, Tridonic, Lunatone)
docker run --rm --device /dev/hidraw0 -p 8080:8080 dali-sim \
  serve office-16 --host 0.0.0.0 --adapter hasseb:/dev/hidraw0 --bridge-mode controller

# serial interfaces (DIY firmware, Raspberry Pi HAT on /dev/serial0)
docker run --rm --device /dev/ttyUSB0 -p 8080:8080 dali-sim \
  serve office-16 --host 0.0.0.0 --adapter serial:/dev/ttyUSB0
```

The container runs as user `dali` (uid 1000). The device node must be readable and writable for that user: add a udev rule, or run with `--group-add` and the device's group id.

## Several DALI lines

One container can simulate several lines (see "Several DALI lines" in [configuration.md](configuration.md#several-dali-lines)). Publish one port per line:

```bash
docker run --rm -p 8080:8080 -p 5555-5557:5555-5557 -p 55825-55827:55825-55827 dali-sim \
  serve three-lines --host 0.0.0.0 --daliserver-port 55825
```

For one fake USB stick per line: `--user root --device /dev/uhid ... --usb-stick tridonic,tridonic,tridonic`.
