"""A relay that counts, and does nothing else.

It sits between the agent and the model API. It forwards the request as it is,
streams the response back byte for byte, and records the counters the provider
reports itself.

What it stores: input and output tokens, the share served from cache, duration,
status code, requested path. What it never stores: request bodies, response
bodies, and the API key, which only passes through.

One port per label. That is what makes consumption attributable to an agent
without ever reading what it exchanges.
"""

from __future__ import annotations

import http.client
import http.server
import json
import socketserver
import ssl
import sys
import threading
import time
import tomllib
from pathlib import Path
from urllib.parse import urlparse

from .store import Store
from .usage import extract

CHUNK = 8192
STREAM_TAIL = 256 * 1024        # tail kept so the trailing usage event can be read
BODY_CAP = 8 * 1024 * 1024      # full body kept when not streaming
DROP_UPSTREAM = {"host", "content-length", "connection", "accept-encoding"}
DROP_DOWNSTREAM = {"transfer-encoding", "connection", "content-length", "content-encoding"}


class Relay(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    label = "?"
    upstream = urlparse("https://api.openai.com")
    store: Store | None = None

    def log_message(self, *args):  # the relay keeps no request log
        pass

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_DELETE(self):
        self._forward("DELETE")

    def _read_request(self) -> tuple[bytes, str | None, bool]:
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        model, streaming = None, False
        if body:
            try:
                payload = json.loads(body)
            except ValueError:
                return body, None, False
            model = payload.get("model")
            streaming = bool(payload.get("stream"))
            # OpenAI-compatible APIs only report usage while streaming if asked.
            # Anthropic always reports it and would reject this field.
            if streaming and "chat/completions" in self.path:
                options = payload.setdefault("stream_options", {})
                if isinstance(options, dict) and "include_usage" not in options:
                    options["include_usage"] = True
                    body = json.dumps(payload).encode()
        return body, model, streaming

    def _forward(self, method: str):
        started = time.time()
        body, model, streaming = self._read_request()

        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP_UPSTREAM}
        headers["Content-Length"] = str(len(body))
        headers["Accept-Encoding"] = "identity"  # otherwise the body comes back compressed

        try:
            if self.upstream.scheme == "https":
                cx = http.client.HTTPSConnection(
                    self.upstream.hostname, self.upstream.port or 443,
                    timeout=900, context=ssl.create_default_context())
            else:
                cx = http.client.HTTPConnection(
                    self.upstream.hostname, self.upstream.port or 80, timeout=900)
            cx.request(method, self.path, body=body, headers=headers)
            response = cx.getresponse()
        except Exception:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self._record(model, 502, time.time() - started, None)
            return

        self.send_response(response.status)
        for k, v in response.getheaders():
            if k.lower() not in DROP_DOWNSTREAM:
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        tail, whole, size = b"", b"", 0
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            try:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            except Exception:
                break  # client gone: stop relaying, still record what happened
            size += len(chunk)
            tail = (tail + chunk)[-STREAM_TAIL:]
            if not streaming and size <= BODY_CAP:
                whole += chunk
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        cx.close()

        source = whole if (not streaming and whole) else tail
        self._record(model, response.status, time.time() - started, extract(source))

    def _record(self, model, status, duration, usage):
        """A failure in the meter must never break a call already answered."""
        if not self.store:
            return
        try:
            self.store.record(self.label, model, self.path, status, duration, usage)
        except Exception:
            pass


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(config_path: str) -> None:
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    store = Store(config.get("database", "./usage.db"))
    listeners = config.get("listener", [])
    if not listeners:
        sys.exit("no listener declared in the configuration")

    threads = []
    for listener in listeners:
        handler = type(
            f"Relay_{listener['label']}",
            (Relay,),
            {"label": listener["label"],
             "upstream": urlparse(listener["upstream"]),
             "store": store},
        )
        bind = listener.get("bind", "127.0.0.1")
        server = Server((bind, listener["port"]), handler)
        print(f"  {listener['label']:12} {bind}:{listener['port']}  ->  {listener['upstream']}")
        threads.append(threading.Thread(target=server.serve_forever, daemon=True))

    for t in threads:
        t.start()
    print(f"  database: {store.path}")
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        pass


def main() -> None:
    config = sys.argv[1] if len(sys.argv) > 1 else "agent-meter.toml"
    if not Path(config).exists():
        sys.exit(f"configuration not found: {config}")
    serve(config)


if __name__ == "__main__":
    main()
