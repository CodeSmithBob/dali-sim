# Security policy

dali-sim's web UI, REST API, WebSocket and TCP API have **no authentication**. They bind to `127.0.0.1` unless you pass `--host`. When a hardware bridge is running, anyone who can reach these ports can switch real lights. Only expose them on trusted networks, or put an authenticating reverse proxy in front.

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's "Report a vulnerability" (private security advisory) on this repository. We aim to answer within a week.
