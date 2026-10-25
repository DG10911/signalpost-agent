from __future__ import annotations

import http.client
import io
import json
import hashlib
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_ms: int
    bytes_received: int
    body: Any = None
    error: str | None = None
    content_sha256: str | None = None
    retrieved_at: str | None = None
    effective_at: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_LOCAL = threading.local()
_UA = "builderr-signalpost-poc/0.1 (+https://builderr.ai)"


class _Status(Exception):
    def __init__(self, code: int, headers: Any, raw: bytes) -> None:
        super().__init__(f"HTTP {code}")
        self.code, self.headers, self.raw = code, headers, raw


def _keepalive_get(url: str, timeout: float) -> tuple[int, bytes]:
    """GET over one persistent HTTPS connection per thread and host.

    A fresh TCP+TLS handshake per registry call (~6 per company) is the pattern
    connection-rate limiters drop; reusing the connection keeps a 1,000-company
    run at a handful of connections per worker. Raises ``_Status`` for non-2xx
    and ``OSError``/``HTTPException`` on transport errors (caller retries)."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise OSError("keep-alive path only handles https")
    key = (parts.hostname, parts.port or 443)
    pool = getattr(_LOCAL, "pool", None)
    if pool is None:
        pool = _LOCAL.pool = {}
    conn = pool.get(key)
    if conn is None:
        conn = pool[key] = http.client.HTTPSConnection(key[0], key[1], timeout=timeout)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        conn.request("GET", path, headers={"Accept": "application/json", "User-Agent": _UA,
                                           "Connection": "keep-alive"})
        resp = conn.getresponse()
        raw = resp.read()
    except Exception:
        conn.close()
        pool.pop(key, None)
        raise
    if resp.getheader("Connection", "").lower() == "close":
        conn.close()
        pool.pop(key, None)
    if 300 <= resp.status < 400:
        raise OSError("redirect")  # let urllib follow it
    if resp.status >= 400:
        raise _Status(resp.status, resp, raw)
    return resp.status, raw


def fetch_json(url: str, *, timeout: float = 20.0, attempts: int = 3) -> FetchResult:
    last_error = "request failed"
    for attempt in range(attempts):
        started = time.monotonic()
        wait = 0.4 * (2 ** attempt)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "builderr-signalpost-poc/0.1 (+https://builderr.ai)"},
        )
        try:
            try:
                status, raw = _keepalive_get(url, timeout)
            except _Status as exc:
                raise urllib.error.HTTPError(url, exc.code, f"HTTP {exc.code}", exc.headers, io.BytesIO(exc.raw))
            except (OSError, http.client.HTTPException):
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    status, raw = response.status, response.read()
            elapsed = int((time.monotonic() - started) * 1000)
            return FetchResult(url, status, elapsed, len(raw), json.loads(raw), content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
        except urllib.error.HTTPError as exc:
            elapsed = int((time.monotonic() - started) * 1000)
            raw = exc.read()
            if exc.code in {404, 410}:
                return FetchResult(url, exc.code, elapsed, len(raw), error=f"HTTP {exc.code}", content_sha256=hashlib.sha256(raw).hexdigest(), retrieved_at=_utc_now())
            last_error = f"HTTP {exc.code}"
            if exc.code == 429:
                # Honour Retry-After when the origin asks us to slow down.
                retry_after = (exc.headers or {}).get("Retry-After") if getattr(exc, "headers", None) else None
                try:
                    wait = min(5.0, float(retry_after)) if retry_after is not None else 1.5 * (attempt + 1)
                except (TypeError, ValueError):
                    wait = 1.5 * (attempt + 1)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            time.sleep(wait)
    return FetchResult(url, 0, 0, 0, error=last_error, retrieved_at=_utc_now())
