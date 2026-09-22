"""Full chain: fake provider, relay, store, report.

It checks what matters: that the relay hands the response back untouched, and
that it records the usage without inventing any of it.
"""

import http.server
import json
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_meter.store import Store

UPSTREAM_PORT, RELAY_PORT = 9971, 9972
RESPONSE = {
    "id": "test", "model": "model-flash",
    "choices": [{"message": {"role": "assistant", "content": "hello"}}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 50,
              "prompt_tokens_details": {"cached_tokens": 800},
              "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200},
}


class Upstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    received = {}

    def log_message(self, *args):
        pass

    refuse = False

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Upstream.received = json.loads(self.rfile.read(n) or b"{}")
        if Upstream.refuse:
            payload = json.dumps(
                {"error": {"message": "unsupported parameter: reasoning_effort",
                           "type": "invalid_request_error"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        payload = json.dumps(RESPONSE).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def cleanup(db: Path, config: Path) -> None:
    config.unlink(missing_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)


def main() -> int:
    db = ROOT / "tests" / "scratch.db"
    config = ROOT / "tests" / "scratch.toml"
    cleanup(db, config)
    config.write_text(
        f'database = "{db}"\n\n'
        f'[[listener]]\nlabel = "test"\nport = {RELAY_PORT}\n'
        f'upstream = "http://127.0.0.1:{UPSTREAM_PORT}"\n\n'
        '[[price]]\nmatch = "flash"\ncache_hit = 0.003\ncache_miss = 0.15\noutput = 0.60\n'
    )

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    upstream = socketserver.ThreadingTCPServer(("127.0.0.1", UPSTREAM_PORT), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()

    relay = subprocess.Popen([sys.executable, "-m", "agent_meter.proxy", str(config)],
                             cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(1.5)

    failures = []
    try:
        body = json.dumps({"model": "model-flash", "stream": True,
                           "messages": [{"role": "user", "content": "hi"}]}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{RELAY_PORT}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"})
        with urllib.request.urlopen(req, timeout=20) as r:
            returned = json.loads(r.read())

        if returned != RESPONSE:
            failures.append("the response handed to the client differs from upstream")
        if Upstream.received.get("stream_options", {}).get("include_usage") is not True:
            failures.append("include_usage was not injected on a streaming request")
        if Upstream.received.get("messages") != [{"role": "user", "content": "hi"}]:
            failures.append("the relay altered the request body")

        time.sleep(0.5)
        rows = Store(db).read(0)
        if len(rows) != 1:
            failures.append(f"{len(rows)} row(s) recorded instead of one")
        else:
            row = rows[0]
            expected = {"input_total": 1000, "cache_read": 800, "input_fresh": 200,
                        "output": 50, "status": 200, "label": "test",
                        "model": "model-flash", "path": "/v1/chat/completions"}
            for key, value in expected.items():
                if row[key] != value:
                    failures.append(f"{key} = {row[key]!r}, expected {value!r}")

        Upstream.refuse = True
        refused_body = json.dumps({"model": "model-flash",
                                   "messages": [{"role": "user", "content": "hi"}]}).encode()
        refused_req = urllib.request.Request(
            f"http://127.0.0.1:{RELAY_PORT}/v1/chat/completions", data=refused_body,
            headers={"Content-Type": "application/json", "Authorization": "Bearer dummy"})
        try:
            urllib.request.urlopen(refused_req, timeout=20)
            failures.append("a 400 from upstream was not passed back to the client")
        except urllib.error.HTTPError as e:
            if e.code != 400:
                failures.append(f"upstream 400 surfaced as {e.code}")
        Upstream.refuse = False

        time.sleep(0.5)
        refused_rows = [r for r in Store(db).read(0) if (r["status"] or 0) >= 400]
        if len(refused_rows) != 1:
            failures.append(f"{len(refused_rows)} refusal(s) recorded instead of one")
        elif "reasoning_effort" not in (refused_rows[0]["error"] or ""):
            failures.append(f"provider message not kept: {refused_rows[0]['error']!r}")

        report = subprocess.run(
            [sys.executable, "-m", "agent_meter.report", str(config), "all"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        if "test" not in report.stdout or "0.0001" not in report.stdout:
            failures.append("the report does not cost the call")
            print(report.stdout, report.stderr)
    finally:
        relay.terminate()
        upstream.shutdown()
        cleanup(db, config)

    for f in failures:
        print("  FAIL:", f)
    print("  ok   full chain" if not failures else f"  {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
