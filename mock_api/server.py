"""Start the mock vendor-risk API for scripts that need it (eval runners) if it is not already running."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def _healthy(base: str) -> bool:
    try:
        return requests.get(f"{base}/health", timeout=0.5).ok
    except requests.RequestException:
        return False


@contextlib.contextmanager
def ensure_running():
    base = os.getenv("VENDOR_RISK_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
    if _healthy(base):
        yield
        return
    port = base.rsplit(":", 1)[-1]
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_api.app:app", "--host", "127.0.0.1", "--port", port,
                             "--log-level", "warning"], cwd=ROOT)
    try:
        deadline = time.monotonic() + 15
        while not _healthy(base):
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"Could not start the mock vendor-risk API on {base}")
            time.sleep(0.25)
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=5)
