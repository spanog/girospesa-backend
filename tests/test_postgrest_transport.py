"""Exercise the real pinned Supabase transport in an isolated interpreter."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run_case(source: str) -> None:
    result = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_supabase_uses_http1_pool_for_both_key_formats():
    run_case('''
from core.supabase_client import create_supabase_client
for key in ["sb_secret_test_key", "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.test"]:
    client = create_supabase_client("https://example.supabase.co", key)
    rest = client.postgrest
    assert rest.session._transport._pool._http2 is False
    assert rest.session.headers["Authorization"] == "Bearer " + key
    assert rest.session.headers["apiKey"] == key
    assert rest.session.headers["Accept-Profile"] == "public"
    rest.aclose()
''')


def test_concurrent_postgrest_reads_recover_after_broken_connection():
    run_case('''
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import httpx
from core.supabase_client import create_supabase_client
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/rest/v1/drop":
            self.close_connection = True
            return
        assert self.headers["Authorization"] == "Bearer sb_secret_test_key"
        payload = b'[{"id":"item"}]'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = Thread(target=server.serve_forever, daemon=True)
thread.start()
client = create_supabase_client(f"http://127.0.0.1:{server.server_port}", "sb_secret_test_key")
rest = client.postgrest
try:
    try:
        rest.session.get("/drop")
        raise AssertionError("expected disconnected response")
    except httpx.RemoteProtocolError:
        pass
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(lambda _: client.table("items").select("id").execute().data, range(16)))
    assert results == [[{"id":"item"}]] * 16
finally:
    rest.aclose()
    server.shutdown()
    server.server_close()
''')
