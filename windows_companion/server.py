import argparse
import json
import os
import signal
import socket
import socketserver
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

DISCOVERY_PORT = 8989
TCP_PORT = 8988
DISCOVERY_LEGACY_MESSAGE = "NOVA_DISCOVER"
PROTOCOL = "nova-pc-controller"
PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 4096
MAX_ACTIVITY_ITEMS = 100


@dataclass
class ClientSession:
    authenticated: bool = False


@dataclass
class ServerState:
    server_name: str
    host: str
    tcp_port: int
    udp_port: int
    pairing_token: str
    started_at: float = field(default_factory=time.time)
    active_clients: set = field(default_factory=set)
    recent_activity: deque = field(default_factory=lambda: deque(maxlen=MAX_ACTIVITY_ITEMS))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_activity(self, action: str) -> None:
        record = {
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.lock:
            self.recent_activity.appendleft(record)

    def clear_activity(self) -> None:
        with self.lock:
            self.recent_activity.clear()

    def get_activity(self):
        with self.lock:
            return list(self.recent_activity)

    def register_client(self, address: str) -> None:
        with self.lock:
            self.active_clients.add(address)

    def unregister_client(self, address: str) -> None:
        with self.lock:
            self.active_clients.discard(address)

    def status(self) -> Dict[str, Any]:
        with self.lock:
            active_count = len(self.active_clients)
        return {
            "server_name": self.server_name,
            "host": self.host,
            "tcp_port": self.tcp_port,
            "udp_port": self.udp_port,
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "requires_authentication": True,
            "active_clients": active_count,
            "uptime_seconds": int(time.time() - self.started_at),
        }


def mask_token(token: str) -> str:
    if not token:
        return "<empty>"
    if len(token) <= 4:
        return "*" * len(token)
    return f"{token[:2]}{'*' * (len(token) - 4)}{token[-2:]}"


def validate_discovery_payload(payload: bytes) -> bool:
    text = payload.decode("utf-8", errors="ignore").strip()
    if text == DISCOVERY_LEGACY_MESSAGE:
        return True
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return False

    return (
        isinstance(message, dict)
        and message.get("type") == "discover"
        and message.get("protocol") == PROTOCOL
    )


def build_response(ok: bool, data: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response: Dict[str, Any] = {"ok": ok, "timestamp": datetime.now(timezone.utc).isoformat()}
    if data is not None:
        response["data"] = data
    if error is not None:
        response["error"] = error
    return response


def handle_command(request: Dict[str, Any], session: ClientSession, state: ServerState) -> Dict[str, Any]:
    command = request.get("command")
    if not isinstance(command, str) or not command:
        return build_response(False, error={"code": "BAD_REQUEST", "message": "Missing or invalid command"})

    if command == "authenticate":
        token = request.get("token")
        if not isinstance(token, str):
            return build_response(False, error={"code": "BAD_REQUEST", "message": "Missing token"})
        if token != state.pairing_token:
            return build_response(False, error={"code": "UNAUTHORIZED", "message": "Invalid pairing token"})
        session.authenticated = True
        state.add_activity("Client authenticated")
        return build_response(True, data={"message": "Authenticated"})

    if not session.authenticated:
        return build_response(
            False,
            error={"code": "UNAUTHORIZED", "message": "Authenticate first"},
        )

    if command == "ping":
        state.add_activity("Ping")
        return build_response(True, data={"message": "pong"})

    if command == "get_status":
        state.add_activity("Status requested")
        return build_response(True, data=state.status())

    if command == "list_features":
        state.add_activity("Features listed")
        return build_response(
            True,
            data={
                "features": [
                    "connection_status",
                    "recent_activity",
                    "audio_streaming_placeholder",
                ]
            },
        )

    if command == "recent_activity":
        return build_response(True, data={"items": state.get_activity()})

    if command == "clear_activity":
        state.clear_activity()
        state.add_activity("Activity cleared")
        return build_response(True, data={"message": "Activity cleared"})

    if command == "disconnect":
        state.add_activity("Client disconnected")
        return build_response(True, data={"message": "Goodbye"})

    return build_response(False, error={"code": "UNKNOWN_COMMAND", "message": f"Unsupported command: {command}"})


class NovaTCPHandler(socketserver.BaseRequestHandler):
    def setup(self) -> None:
        self.session = ClientSession()
        self.client_id = f"{self.client_address[0]}:{self.client_address[1]}"
        self.server.state.register_client(self.client_id)
        print(f"[TCP] Client connected: {self.client_id}")

    def handle(self) -> None:
        reader = self.request.makefile("rb")
        writer = self.request.makefile("wb")
        while True:
            raw_line = reader.readline(MAX_REQUEST_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > MAX_REQUEST_BYTES:
                response = build_response(False, error={"code": "TOO_LARGE", "message": "Request too large"})
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                writer.flush()
                continue

            try:
                payload = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response = build_response(False, error={"code": "BAD_JSON", "message": "Invalid JSON request"})
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                writer.flush()
                continue

            if not isinstance(payload, dict):
                response = build_response(False, error={"code": "BAD_REQUEST", "message": "JSON body must be an object"})
                writer.write((json.dumps(response) + "\n").encode("utf-8"))
                writer.flush()
                continue

            response = handle_command(payload, self.session, self.server.state)
            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            writer.flush()

            if payload.get("command") == "disconnect":
                break

    def finish(self) -> None:
        self.server.state.unregister_client(self.client_id)
        print(f"[TCP] Client disconnected: {self.client_id}")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True, state: Optional[ServerState] = None):
        self.state = state
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)


class UDPDiscoveryResponder(threading.Thread):
    def __init__(self, state: ServerState, shutdown_event: threading.Event):
        super().__init__(daemon=True)
        self.state = state
        self.shutdown_event = shutdown_event
        self.sock: Optional[socket.socket] = None

    def run(self) -> None:
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.state.host, self.state.udp_port))
            self.sock.settimeout(1.0)
            print(f"[UDP] Discovery listening on {self.state.host}:{self.state.udp_port}")

            while not self.shutdown_event.is_set():
                try:
                    data, addr = self.sock.recvfrom(2048)
                except socket.timeout:
                    continue
                except OSError as exc:
                    if self.shutdown_event.is_set():
                        break
                    print(f"[UDP] Socket error: {exc}")
                    continue

                if not validate_discovery_payload(data):
                    continue

                response = {
                    "type": "discover_response",
                    "protocol": PROTOCOL,
                    "protocol_version": PROTOCOL_VERSION,
                    "server_name": self.state.server_name,
                    "ip": self.state.host,
                    "tcp_port": self.state.tcp_port,
                    "requires_authentication": True,
                }
                try:
                    self.sock.sendto(json.dumps(response).encode("utf-8"), addr)
                    print(f"[UDP] Discovery response sent to {addr[0]}:{addr[1]}")
                except OSError as exc:
                    print(f"[UDP] Failed to send discovery response: {exc}")
        finally:
            if self.sock is not None:
                self.sock.close()


class NovaServer:
    def __init__(self, host: str, tcp_port: int, udp_port: int, server_name: str, pairing_token: str):
        self.shutdown_event = threading.Event()
        self.state = ServerState(
            server_name=server_name,
            host=host,
            tcp_port=tcp_port,
            udp_port=udp_port,
            pairing_token=pairing_token,
        )
        self.tcp_server = ThreadedTCPServer((host, tcp_port), NovaTCPHandler, state=self.state)
        self.udp_responder = UDPDiscoveryResponder(self.state, self.shutdown_event)

    def start(self) -> None:
        print(f"[NOVA] Starting server '{self.state.server_name}'")
        print(f"[NOVA] TCP command server: {self.state.host}:{self.state.tcp_port}")
        print(f"[NOVA] UDP discovery server: {self.state.host}:{self.state.udp_port}")
        print(f"[NOVA] Pairing token (masked): {mask_token(self.state.pairing_token)}")
        self.udp_responder.start()
        tcp_thread = threading.Thread(target=self.tcp_server.serve_forever, daemon=True)
        tcp_thread.start()

    def stop(self) -> None:
        if self.shutdown_event.is_set():
            return
        print("[NOVA] Stopping server...")
        self.shutdown_event.set()
        self.tcp_server.shutdown()
        self.tcp_server.server_close()
        print("[NOVA] Server stopped")


def create_default_token() -> str:
    token = os.environ.get("NOVA_PAIRING_TOKEN", "").strip()
    if token:
        return token
    return os.urandom(16).hex()


def main() -> None:
    parser = argparse.ArgumentParser(description="NOVA PC Controller Windows Companion Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP command port")
    parser.add_argument("--udp-port", type=int, default=DISCOVERY_PORT, help="UDP discovery port")
    parser.add_argument("--name", default=socket.gethostname(), help="Server display name")
    parser.add_argument(
        "--pairing-token",
        default="",
        help="Optional pairing token. If omitted, NOVA_PAIRING_TOKEN env var is used, otherwise a random token is generated.",
    )
    args = parser.parse_args()

    pairing_token = args.pairing_token.strip() or create_default_token()
    server = NovaServer(
        host=args.host,
        tcp_port=args.tcp_port,
        udp_port=args.udp_port,
        server_name=args.name,
        pairing_token=pairing_token,
    )

    def handle_signal(signum, _frame):
        print(f"[NOVA] Received signal {signum}; shutting down")
        server.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    server.start()

    try:
        while not server.shutdown_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
