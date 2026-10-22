"""Zetaris federated data layer.

When Zetaris is configured, public-API reads go through it instead of straight to the source: each
request is registered as a Zetaris REST session table and read back with Lightning SQL. For literature,
the publication-date cut-off is also applied in that SQL, so documents dated after `as_of` are filtered
by the data layer itself, not only by the agent code.

Configuration (Settings page or .env): ZETARIS_REST_URL (API base ending in /api/v1.0), ZETARIS_ORG_ID
(numeric organisation ID) and ZETARIS_API_KEY (created in the Zetaris UI under Management -> API Tokens).
Any Zetaris failure falls back to the direct request; SAMPLE_MODE never calls Zetaris.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date
from urllib.parse import urlencode

import httpx

from app import config

DEFAULT_REST_URL = "http://localhost:8888/api/v1.0"
TIMEOUT = httpx.Timeout(120.0, connect=5.0)


class ZetarisError(RuntimeError):
    pass


def settings() -> dict:
    return {"url": os.getenv("ZETARIS_REST_URL", DEFAULT_REST_URL).rstrip("/"),
            "org": os.getenv("ZETARIS_ORG_ID", "").strip(),
            "key": os.getenv("ZETARIS_API_KEY", "").strip()}


def configured() -> bool:
    s = settings()
    return bool(s["key"]) and not s["key"].startswith("your-") and s["org"].isdigit()


def enabled() -> bool:
    return configured() and config.source_enabled("zetaris") and not config.sample_mode()


def sql(statement: str, limit: int = 1000) -> list[list]:
    """Run one Lightning SQL statement over the Zetaris REST API and return its rows."""
    s = settings()
    headers = {"Authorization": f"Bearer {s['key']}", "X-Org-ID": s["org"],
               "X-Request-ID": str(uuid.uuid4()), "Content-Type": "application/json"}
    body = {"queryId": str(uuid.uuid4()), "sql": statement, "source": "SqlEditor", "limit": limit}
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(f"{s['url']}/sql-editor/sqls/run", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise ZetarisError(f"cannot reach Zetaris at {s['url']}: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise ZetarisError(f"Zetaris HTTP {response.status_code}: {response.text[:300]}")
    out = response.json() if response.content else {}
    rows = out.get("data") if isinstance(out, dict) else None
    return rows or []


def _quote(value: str) -> str:
    if '"' in value:
        raise ZetarisError("endpoint contains a double quote")
    return f'"{value}"'


def _session_table(url: str) -> str:
    name = "ta_" + uuid.uuid4().hex[:16]
    agent = re.sub(r'["\\]', "", f"trial-autopsy/0.1 ({config.contact_email()})")  # SEC EDGAR requires a contact
    sql(f"CREATE REST SESSION TABLE {name} REQUEST (endpoint {_quote(url)}, method \"GET\", "
        f"http_encoding \"URLENCODED\", response_type \"JSON\") HEADER (user-agent {_quote(agent)}) BODY ()")
    return name


def _drop(name: str):
    try:
        sql(f"DROP TABLE session.{name}")
    except ZetarisError:
        pass


def full_url(url: str, params: dict | None) -> str:
    return f"{url}?{urlencode(params)}" if params else url


def get_json(url: str, params: dict | None = None):
    """The JSON body of a GET request, read through a Zetaris REST session table."""
    name = _session_table(full_url(url, params))
    try:
        rows = sql(f"SELECT to_json(struct(*)) FROM session.{name}", limit=1)
    finally:
        _drop(name)
    if not rows or not rows[0] or rows[0][0] is None:
        raise ZetarisError("Zetaris returned no rows")
    return json.loads(rows[0][0])


def europepmc_search(url: str, params: dict, as_of: date | None) -> dict:
    """Europe PMC search through Zetaris, with the publication-date cut-off applied in Lightning SQL."""
    name = _session_table(full_url(url, params))
    cutoff = f"WHERE r.firstPublicationDate <= '{as_of.isoformat()}'" if as_of else ""
    try:
        rows = sql(f"SELECT to_json(r) FROM session.{name} LATERAL VIEW explode(resultList.result) AS r {cutoff}")
    finally:
        _drop(name)
    return {"resultList": {"result": [json.loads(row[0]) for row in rows if row and row[0]]}}


def status() -> dict:
    """Connection check for the Settings page."""
    if not configured():
        return {"ok": False, "message": "set the Zetaris API URL, organisation ID and API key"}
    try:
        rows = sql("SELECT 1")
        ok = bool(rows) and str(rows[0][0]) in ("1", "1.0")
        return {"ok": ok, "message": "connected: SELECT 1 answered" if ok else f"unexpected reply {rows[:1]}"}
    except ZetarisError as exc:
        return {"ok": False, "message": re.sub(r"Bearer \S+", "Bearer [key]", str(exc))[:300]}
