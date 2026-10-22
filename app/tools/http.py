"""HTTP with timeouts, retries, disk cache and SAMPLE_MODE.

Every response is cached under data/cache/http/. In SAMPLE_MODE no request is
made: a cache miss raises OfflineMiss, which callers report as a structured,
recoverable error.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import httpx

from app import config

TIMEOUT = httpx.Timeout(45.0, connect=10.0)
RETRIES = 3


class OfflineMiss(RuntimeError):
    pass


class SourceError(RuntimeError):
    pass


def _key(method: str, url: str, params: dict | None, body) -> str:
    blob = json.dumps([method, url, params or {}, body], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


def _headers() -> dict:
    return {"User-Agent": f"trial-autopsy/0.1 ({config.contact_email()})", "Accept": "application/json, */*"}


SAMPLE_CACHE = config.DATA / "sample_cache" / "http"


def _log_key(key: str):
    log = os.getenv("HTTP_KEY_LOG")
    if log:
        with open(log, "a") as f:
            f.write(key + "\n")


def _store(path, url: str, body, key: str, via: str | None = None):
    config.HTTP_CACHE.mkdir(parents=True, exist_ok=True)
    record = {"url": url, "fetched_at": time.time(), "body": body}
    if via:
        record["via"] = via
    path.write_text(json.dumps(record, ensure_ascii=False))
    _log_key(key)


def _via_zetaris(read, method: str, url: str, params: dict | None, as_text: bool, tracer, source: str):
    """Read a GET JSON request through the Zetaris data layer; None means fall back to the direct request."""
    from app.tools import zetaris
    if method != "GET" or as_text or not zetaris.enabled():
        return None
    try:
        body = (read or zetaris.get_json)(url, params)
    except Exception as exc:  # Zetaris is an optional layer: any failure falls back to the source itself
        if tracer:
            tracer.log("Tools", "zetaris fallback", input_summary=zetaris.full_url(url, params),
                       output_summary=f"{type(exc).__name__}: {exc}"[:300], status="warning", source=source)
        return None
    if tracer:
        tracer.log("Tools", "read via Zetaris", input_summary=zetaris.full_url(url, params),
                   output_summary=f"{source} federated through Zetaris Lightning SQL", source=source, via="zetaris")
    return body


def request(method: str, url: str, *, params: dict | None = None, json_body=None, as_text: bool = False,
            tracer=None, source: str = "http", zetaris_read=None):
    key = _key(method, url, params, json_body)
    path = config.HTTP_CACHE / f"{key}.json"
    for candidate in (path, SAMPLE_CACHE / f"{key}.json"):
        if candidate.exists():
            _log_key(key)
            return json.loads(candidate.read_text())["body"]
    if config.sample_mode():
        raise OfflineMiss(f"{source}: not in offline cache ({url})")

    body = _via_zetaris(zetaris_read, method, url, params, as_text, tracer, source)
    if body is not None:
        _store(path, url, body, key, via="zetaris")
        return body

    last_error = None
    for attempt in range(RETRIES):
        try:
            with httpx.Client(timeout=TIMEOUT, headers=_headers(), follow_redirects=True) as client:
                response = client.request(method, url, params=params, json=json_body)
            if response.status_code in (429, 500, 502, 503, 504):
                raise SourceError(f"{source} HTTP {response.status_code}")
            if response.status_code >= 400:
                raise SourceError(f"{source} HTTP {response.status_code}: {response.text[:200]}")
            if response.status_code == 204 or not response.content:
                body = "" if as_text else {}      # "nothing found" is an answer: cache it, don't retry
            else:
                body = response.text if as_text else response.json()
            _store(path, str(response.url), body, key)
            return body
        except (httpx.HTTPError, SourceError, ValueError) as exc:
            last_error = exc
            if isinstance(exc, SourceError) and "HTTP 4" in str(exc) and "429" not in str(exc):
                break
            if tracer:
                tracer.log("Tools", "retry", input_summary=url, output_summary=str(exc), retry_count=attempt + 1,
                           status="retry", source=source)
            time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"{source} failed after retries: {last_error}")


def get_json(url: str, params: dict | None = None, **kw):
    return request("GET", url, params=params, **kw)


def post_json(url: str, body, **kw):
    return request("POST", url, json_body=body, **kw)


def get_text(url: str, params: dict | None = None, **kw) -> str:
    return request("GET", url, params=params, as_text=True, **kw)
