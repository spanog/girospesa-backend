"""Exercise real FastAPI dispatch in an isolated interpreter (legacy tests stub imports)."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROBE = r'''
import asyncio, importlib, threading
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import httpx

module = importlib.import_module("api.routers." + __import__("sys").argv[1])
handler = getattr(module, __import__("sys").argv[2])
entered, release = threading.Event(), threading.Event()
class Completed(Exception): pass

def slow_io(*args, **kwargs):
    entered.set()
    if not release.wait(2): raise AssertionError("database blocked the event loop")
    raise Completed()

if hasattr(module, "get_supabase"): module.get_supabase = slow_io
else: module.repo.list_notifications = slow_io
app = FastAPI()
app.add_api_route("/probe", handler, methods=["GET"])
for dependency in app.routes[-1].dependant.dependencies:
    app.dependency_overrides[dependency.call] = lambda: "test-user"
@app.exception_handler(Completed)
async def completed(request, error): return JSONResponse({"ok": True})
@app.get("/ready")
async def ready(): return {"ok": True}

async def verify():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        pending = asyncio.create_task(client.get("/probe?list_id=test-list"))
        try:
            assert await asyncio.to_thread(entered.wait, 1), "probe did not reach database"
            assert not pending.done(), "blocking I/O finished before event loop resumed"
            assert (await client.get("/ready")).status_code == 200
            assert not pending.done(), "unrelated request should finish while database is suspended"
        finally:
            release.set()
            await pending
asyncio.run(verify())
'''

@pytest.mark.parametrize("module,handler", [
    ("users", "get_profile"),
    ("lists", "list_lists"),
    ("lists", "get_active_list"),
    ("lists", "list_members"),
    ("offers", "list_public_offers"),
    ("offers", "discover_public_offers"),
    ("flyers", "list_public_flyers"),
    ("flyers", "discover_public_flyers"),
    ("supermarkets", "list_supermarkets"),
    ("notifications", "list_notifications"),
])
def test_startup_read_does_not_block_unrelated_requests(module, handler):
    result = subprocess.run([sys.executable, "-c", PROBE, module, handler], cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
