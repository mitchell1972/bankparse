"""
BankParse — Turso HTTP Client
Lightweight HTTP client for Turso's pipeline API.
Zero external dependencies — uses only urllib + json + ssl.
Compatible with Vercel serverless (no native C extensions needed).
"""

import json
import logging
import ssl
import urllib.request
import urllib.error
from typing import Any

logger = logging.getLogger("bankparse.turso")


class TursoHTTPError(Exception):
    """Error from the Turso HTTP API."""
    pass


class TursoRow:
    """A row from a Turso query result, accessible by index or column name."""
    __slots__ = ("_values", "_col_map")

    def __init__(self, values: list, col_map: dict[str, int]):
        self._values = values
        self._col_map = col_map

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._col_map[key]]
        return self._values[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self):
        return self._col_map.keys()

    def to_dict(self) -> dict:
        return {col: self._values[idx] for col, idx in self._col_map.items()}


class TursoResult:
    """Result from a Turso query execution."""
    __slots__ = ("columns", "rows", "affected_row_count", "last_insert_rowid")

    def __init__(self, result_data: dict):
        cols = result_data.get("cols", [])
        self.columns = [c["name"] for c in cols]
        col_map = {c["name"]: i for i, c in enumerate(cols)}

        raw_rows = result_data.get("rows", [])
        self.rows = []
        for raw_row in raw_rows:
            values = [self._parse_value(v) for v in raw_row]
            self.rows.append(TursoRow(values, col_map))

        self.affected_row_count = result_data.get("affected_row_count", 0)
        rid = result_data.get("last_insert_rowid")
        self.last_insert_rowid = int(rid) if rid is not None else None

    @staticmethod
    def _parse_value(v) -> Any:
        """Convert Turso's typed value to Python native type."""
        if v is None:
            return None
        if isinstance(v, dict):
            vtype = v.get("type", "")
            value = v.get("value")
            if vtype == "null" or value is None:
                return None
            if vtype == "integer":
                return int(value)
            if vtype == "float":
                return float(value)
            if vtype == "text":
                return str(value)
            if vtype == "blob":
                return value  # base64 encoded
            return value
        return v

    def fetchone(self) -> TursoRow | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[TursoRow]:
        return self.rows


class TursoHTTPClient:
    """
    Lightweight HTTP client for Turso's /v2/pipeline API.
    Thread-safe, stateless, no native dependencies.
    """

    def __init__(self, url: str, auth_token: str):
        # Convert libsql:// to https://
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self.url = url.rstrip("/")
        self.auth_token = auth_token
        self._pipeline_url = f"{self.url}/v2/pipeline"

        # SSL context — try certifi first, then system certs
        try:
            import certifi
            self._ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            self._ssl_ctx = ssl.create_default_context()

    def _send_pipeline(self, requests: list[dict]) -> list[dict]:
        """Send a batch of requests to the Turso pipeline API."""
        payload = json.dumps({"requests": requests}).encode("utf-8")
        req = urllib.request.Request(
            self._pipeline_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            resp = urllib.request.urlopen(req, context=self._ssl_ctx, timeout=30)
            data = json.loads(resp.read())
            results = data.get("results", [])
            # Check for errors
            for r in results:
                if r.get("type") == "error":
                    err = r.get("error", {})
                    raise TursoHTTPError(f"Turso error: {err.get('message', str(err))}")
            return results
        except urllib.error.URLError as e:
            raise TursoHTTPError(f"Turso connection error: {e}") from e
        except json.JSONDecodeError as e:
            raise TursoHTTPError(f"Turso response parse error: {e}") from e

    def execute(self, sql: str, params: tuple = ()) -> TursoResult:
        """Execute a single SQL statement."""
        stmt = {"sql": sql}
        if params:
            stmt["args"] = [self._encode_param(p) for p in params]

        results = self._send_pipeline([
            {"type": "execute", "stmt": stmt},
            {"type": "close"},
        ])

        for r in results:
            if r.get("type") == "ok" and r.get("response", {}).get("type") == "execute":
                return TursoResult(r["response"]["result"])

        return TursoResult({"cols": [], "rows": []})

    def executemany(self, statements: list[tuple[str, tuple]]) -> list[TursoResult]:
        """Execute multiple SQL statements in a single pipeline request."""
        requests = []
        for sql, params in statements:
            stmt = {"sql": sql}
            if params:
                stmt["args"] = [self._encode_param(p) for p in params]
            requests.append({"type": "execute", "stmt": stmt})
        requests.append({"type": "close"})

        results = self._send_pipeline(requests)

        turso_results = []
        for r in results:
            if r.get("type") == "ok" and r.get("response", {}).get("type") == "execute":
                turso_results.append(TursoResult(r["response"]["result"]))

        return turso_results

    def executescript(self, sql: str):
        """Execute a multi-statement SQL script (for schema creation)."""
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        if not statements:
            return
        requests = [{"type": "execute", "stmt": {"sql": s}} for s in statements]
        requests.append({"type": "close"})
        self._send_pipeline(requests)

    @staticmethod
    def _encode_param(value) -> dict:
        """Encode a Python value for the Turso API."""
        if value is None:
            return {"type": "null", "value": None}
        if isinstance(value, bool):
            return {"type": "integer", "value": str(int(value))}
        if isinstance(value, int):
            return {"type": "integer", "value": str(value)}
        if isinstance(value, float):
            return {"type": "float", "value": value}
        if isinstance(value, bytes):
            import base64
            return {"type": "blob", "base64": base64.b64encode(value).decode()}
        return {"type": "text", "value": str(value)}
