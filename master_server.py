#!/usr/bin/env python3
import argparse
import http.server
import secrets
import socketserver
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HANDLER_PATHS = ("/handlerservers.ashx", "/handlerservers.php")
DEFAULT_PORT = 7240
DEFAULT_TTL_SECONDS = 600
CHALLENGE_TTL_SECONDS = 60


def parse_port(value, default=DEFAULT_PORT):
    try:
        port = int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def parse_server_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    host, sep, port_text = line.rpartition(":")
    port = parse_port(port_text, default=None)
    if not sep or not host or port is None:
        return None
    return f"{host}:{port}"


class ServerRegistry:
    def __init__(self, servers_file, ttl_seconds):
        self.servers_file = Path(servers_file)
        self.ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._active = {}
        self._pending = {}

    def _static_entries(self):
        if not self.servers_file.exists():
            return []
        entries = []
        for line in self.servers_file.read_text(encoding="utf-8").splitlines():
            entry = parse_server_line(line)
            if entry:
                entries.append(entry)
        return entries

    @staticmethod
    def _key(ip, port):
        return f"{ip}:{port}"

    def begin_ping(self, ip, port):
        key = self._key(ip, port)
        now = time.monotonic()
        challenge = secrets.randbelow(2147483646) + 1
        with self._lock:
            self._pending[key] = (challenge, now + CHALLENGE_TTL_SECONDS)
            self._prune(now)
        return challenge

    def confirm_ping(self, ip, port, challenge):
        key = self._key(ip, port)
        now = time.monotonic()
        try:
            challenge = int(challenge)
        except (TypeError, ValueError):
            return False

        with self._lock:
            pending = self._pending.get(key)
            if pending is None or pending[0] != challenge or pending[1] < now:
                self._pending.pop(key, None)
                return False
            self._pending.pop(key, None)
            self._active[key] = now
            self._prune(now)
            return True

    def remove(self, ip, port):
        key = self._key(ip, port)
        with self._lock:
            self._pending.pop(key, None)
            return self._active.pop(key, None) is not None

    def list_entries(self):
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            entries = []
            seen = set()
            for entry in self._static_entries() + sorted(self._active):
                if entry not in seen:
                    seen.add(entry)
                    entries.append(entry)
            return entries

    def _prune(self, now):
        expired_pending = [key for key, value in self._pending.items() if value[1] < now]
        for key in expired_pending:
            self._pending.pop(key, None)

        expired_active = [
            key for key, last_seen in self._active.items()
            if now - last_seen > self.ttl_seconds
        ]
        for key in expired_active:
            self._active.pop(key, None)


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_text(self, body, status=200):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store,no-cache,no-transform")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        handler_path = parsed.path.rstrip("/")
        if handler_path not in HANDLER_PATHS:
            self.send_error(404, "Not Found")
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        request_type = query.get("type", [""])[0]
        client_ip = self.client_address[0]
        port = parse_port(query.get("port", [None])[0])
        if port is None:
            self._send_text("ERROR", status=400)
            return

        if request_type == "ping":
            challenge = self.server.registry.begin_ping(client_ip, port)
            self._send_text(str(challenge))
            return

        if request_type == "confirmping":
            challenge = query.get("rand", [""])[0]
            if self.server.registry.confirm_ping(client_ip, port, challenge):
                self._send_text("1")
            else:
                self._send_text("-1")
            return

        if request_type == "remove":
            self.server.registry.remove(client_ip, port)
            self._send_text("1")
            return

        if request_type == "list":
            entries = self.server.registry.list_entries()
            body = "|".join(entries)
            if entries:
                body += "|"
            self._send_text(body)
            return

        if request_type == "chkserialandroid":
            # The client parses this as: <long long>|<int>.
            # -1 and 0|0 are special error states; 1|1 enters the normal
            # request-to-join path without a real serial signature.
            self._send_text("1|1")
            return

        # The patched dedicated server sends only ping/confirmping/remove.
        self._send_text("")

    def log_message(self, fmt, *args):
        print("[master %s] %s" % (time.strftime("%H:%M:%S"), fmt % args), flush=True)


class MasterServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, registry):
        super().__init__(address, handler)
        self.registry = registry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--servers", default=str(Path(__file__).with_name("servers.txt")))
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL_SECONDS,
                    help="seconds without a confirmed heartbeat before removal")
    args = ap.parse_args()

    registry = ServerRegistry(args.servers, args.ttl)
    with MasterServer((args.listen, args.port), Handler, registry) as httpd:
        print(
            f"[master] listening on http://{args.listen}:{args.port}/handlerservers.ashx "
            f"(heartbeat TTL={args.ttl}s)",
            flush=True,
        )
        httpd.serve_forever()


if __name__ == "__main__":
    main()

