"""
Playwright E2E test harness for bankparse.

Spawns `uvicorn app:app` against a temp SQLite DB on a random port, exports
the base URL to tests, and tears down after the session.

Required env at runtime — populated in the fixture, only inside this process:
    TEST_MODE_ENABLED=1            so /api/test/* endpoints respond
    DATABASE_PATH=<tmp file>       isolated SQLite, not the real DB
    SECRET_KEY=test-secret         deterministic auth cookie signing
    RESEND_API_KEY=""              keeps OTP send in fallback (logs only)

Tests reach the OTP via /api/test/peek-otp instead of an inbox.
"""

from __future__ import annotations

import os
import sys
import socket
import subprocess
import tempfile
import time
from contextlib import closing
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pick_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(url: str, timeout: float = 30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/api/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"Server at {url} did not become healthy within {timeout}s")


@pytest.fixture(scope="session")
def live_server():
    """Spawn uvicorn against an isolated DB. Tear down at session end."""
    port = _pick_free_port()
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()

    env = os.environ.copy()
    env["TEST_MODE_ENABLED"] = "1"
    env["DATABASE_PATH"] = tmp_db.name
    env.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-prod")
    env.setdefault("RESEND_API_KEY", "")  # fallback (logs OTP, doesn't send)
    env.setdefault("STRIPE_SECRET_KEY", "")
    env.setdefault("ANTHROPIC_API_KEY", "")
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            os.unlink(tmp_db.name)
        except OSError:
            pass


@pytest.fixture(scope="session")
def hmrc_live_server():
    """Like ``live_server`` but spawns the stub HMRC server first and points
    the uvicorn subprocess at it via ``HMRC_BASE_URL``.

    Why a separate fixture rather than extending ``live_server``: the
    existing E2E tests must not depend on the stub starting, and the stub
    must be inside the same process tree so we can assert against the
    requests it captured.
    """
    from tests.e2e._hmrc_stub import HmrcStub

    app_port = _pick_free_port()
    stub_port = _pick_free_port()
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()

    with HmrcStub(port=stub_port) as stub:
        env = os.environ.copy()
        env["TEST_MODE_ENABLED"] = "1"
        env["DATABASE_PATH"] = tmp_db.name
        env.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-prod")
        env.setdefault("RESEND_API_KEY", "")
        env.setdefault("STRIPE_SECRET_KEY", "")
        env.setdefault("ANTHROPIC_API_KEY", "")
        # HMRC wiring — these MUST be set before app.py imports hmrc.config,
        # since HMRC_BASE_URL is captured at module load.
        env["HMRC_ENV"] = "sandbox"
        env["HMRC_BASE_URL"] = stub.base_url
        env["HMRC_CLIENT_ID"] = "stub-client-id"
        env["HMRC_CLIENT_SECRET"] = "stub-client-secret"
        env["HMRC_REDIRECT_URI"] = f"http://127.0.0.1:{app_port}/api/hmrc/callback"
        # 32-byte AES-GCM key; deterministic so test reruns reuse the same
        # encrypted token blobs. Generated from a fixed seed — only for tests.
        env["HMRC_TOKEN_ENCRYPTION_KEY"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        env["PYTHONUNBUFFERED"] = "1"

        log_path = tempfile.NamedTemporaryFile(
            suffix=".log", prefix="hmrc_e2e_uvicorn_", delete=False,
        ).name
        log_fh = open(log_path, "w+")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(app_port), "--log-level", "info"],
            cwd=str(REPO_ROOT), env=env,
            stdout=log_fh, stderr=subprocess.STDOUT,
        )

        base_url = f"http://127.0.0.1:{app_port}"
        try:
            _wait_for_health(base_url)
            yield {"base_url": base_url, "stub": stub, "log_path": log_path}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            try:
                log_fh.close()
            except Exception:
                pass
            # Surface uvicorn logs into the pytest report so a failed test
            # doesn't leave the failure mode buried in a tempfile.
            try:
                with open(log_path, "r") as f:
                    tail = f.read()[-8000:]
                print("\n----- uvicorn server log (tail 8 KB) -----")
                print(tail)
                print("----- end uvicorn log -----")
            except Exception:
                pass
            try:
                os.unlink(tmp_db.name)
            except OSError:
                pass
            try:
                os.unlink(log_path)
            except OSError:
                pass


@pytest.fixture(scope="session")
def fixture_pdf() -> Path:
    """Small real bank statement PDF used as the upload fixture."""
    p = REPO_ROOT / "tests" / "fixtures" / "us_statements" / "bofa_sample.pdf"
    if not p.exists():
        pytest.skip(f"fixture PDF missing at {p}")
    return p


@pytest.fixture(scope="session")
def fixture_csv() -> Path:
    """Tiny CSV statement — parsed locally so the E2E test doesn't need an
    Anthropic key."""
    p = REPO_ROOT / "tests" / "e2e" / "fixtures" / "sample_statement.csv"
    if not p.exists():
        pytest.skip(f"fixture CSV missing at {p}")
    return p
