# Nova-PC-Controller

NOVA PC Controller is an Android-to-Windows control system for local Wi-Fi networks.

This repository now includes a production-ready **Windows Companion Server** (Python) with:
- UDP discovery on **8989**
- TCP command channel on **8988**
- Structured JSON request/response protocol
- Client authentication via pairing token
- Multi-client handling (threaded TCP server)
- Graceful shutdown and socket cleanup
- Recent activity tracking API

---

## Repository Structure

- `windows_companion/server.py` — Windows Companion server (UDP discovery + TCP commands)
- `tests/test_server.py` — focused protocol/authentication tests
- `requirements.txt` — Python dependency notes

---

## Current Feature Status

### Implemented (Windows Companion)
- [x] Reliable startup and shutdown
- [x] UDP discovery listener (`8989`) with discovery response payload
- [x] TCP server (`8988`) for command exchange
- [x] Structured responses with success/error schema
- [x] Request size and JSON validation
- [x] Safe command allowlist (no shell execution)
- [x] Client authentication requirement
- [x] Multi-client support through `ThreadingMixIn`
- [x] Connection/activity logging and state tracking

### Android App
Android source files are not present in this repository snapshot, so Android UI and client code could not be directly modified in this change set. The server protocol is ready for Android integration.

---

## Windows Companion Setup

## 1) Prerequisites
- Windows 10/11
- Python 3.9+

## 2) Install dependencies
```bash
pip install -r requirements.txt
```
(There are no third-party Python dependencies currently.)

## 3) Run server
```bash
python windows_companion/server.py
```

Optional flags:
```bash
python windows_companion/server.py --host 0.0.0.0 --tcp-port 8988 --udp-port 8989 --name MY-PC
```

Optional pairing token:
```bash
python windows_companion/server.py --pairing-token your_secure_token
```
Or set env var:
```bash
set NOVA_PAIRING_TOKEN=your_secure_token
```

If no token is provided, one is generated at startup and shown in masked form in logs.

---

## Protocol Overview

### UDP Discovery Request
Either:
- Plain text: `NOVA_DISCOVER`
- JSON: `{"type":"discover","protocol":"nova-pc-controller"}`

### UDP Discovery Response
```json
{
  "type": "discover_response",
  "protocol": "nova-pc-controller",
  "protocol_version": 1,
  "server_name": "MY-PC",
  "ip": "192.168.1.100",
  "tcp_port": 8988,
  "requires_authentication": true
}
```

### TCP Command Flow
Send newline-delimited JSON objects.

1. Authenticate first:
```json
{"command":"authenticate","token":"<pairing-token>"}
```

2. Supported commands:
- `ping`
- `get_status`
- `list_features`
- `recent_activity`
- `clear_activity`
- `disconnect`

Every response:
```json
{
  "ok": true,
  "timestamp": "2026-10-30T00:00:00+00:00",
  "data": {}
}
```
or
```json
{
  "ok": false,
  "timestamp": "2026-10-30T00:00:00+00:00",
  "error": {"code": "UNAUTHORIZED", "message": "Authenticate first"}
}
```

---

## Android Integration Notes

When Android client code is available, implement this workflow:
1. Send UDP discovery request on port 8989.
2. Parse and de-duplicate responses by `ip + tcp_port + server_name`.
3. Display discovered PCs in connection UI.
4. Connect to selected server on TCP 8988.
5. Authenticate using pairing token.
6. Keep socket operations in background threads/coroutines and post UI updates on main thread.

---

## Network Troubleshooting

- Ensure Android device and PC are on the same Wi-Fi subnet.
- Allow inbound UDP **8989** and TCP **8988** in Windows Firewall.
- If discovery fails, verify broadcast/multicast restrictions in router settings.
- If TCP connection fails, test using direct IP and confirm server bind host.

---

## Security Notes

- Server accepts only explicit allowlisted commands.
- No arbitrary shell command execution is implemented.
- Authentication is required before command access.
- Do not expose this server directly to the public internet.

---

## Testing

Run focused tests:
```bash
python -m unittest discover -s tests -v
```

---

## Screenshots

Android screenshot assets are not included yet. Add them here when Android UI is committed.

---

## Roadmap

- Add Android app module (Java/XML) with modern dark dashboard UI.
- Implement Android discovery and connection manager with retries/timeouts.
- Add real audio streaming controls integrated with authenticated server protocol.
- Persist recent activity in Android local storage.
- Add instrumentation and integration tests across Android + Windows components.
