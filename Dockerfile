# dali-sim container: web UI on 8080, JSON-lines TCP API on 5555.
# Build:  docker build -t dali-sim .
# Run:    docker run --rm -p 8080:8080 -p 5555:5555 dali-sim
# With a USB adapter (Linux): add  --device /dev/hidraw0  (or /dev/ttyUSB0)
# Override with --build-arg BASE=... to use a registry mirror.
ARG BASE=python:3.12-slim
FROM ${BASE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# No system packages needed: the hidapi wheel bundles libusb and libudev.
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install ".[hardware]"
COPY examples ./examples

RUN useradd --create-home --uid 1000 dali
USER dali

EXPOSE 8080 5555
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/api/info', timeout=2)" || exit 1

ENTRYPOINT ["dali-sim"]
CMD ["serve", "office-16", "--host", "0.0.0.0", "--port", "8080", "--tcp-port", "5555"]
