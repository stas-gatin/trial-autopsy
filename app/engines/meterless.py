"""Client for the local Meterless World Model service (services/meterless).

The service runs the official Meterless World Model reference engine. Every source, entity and verified
fact the agents write to the World Model is also written there, one namespace per run, so the official
engine keeps an event-sourced, audited and idempotent copy of the case. If the service is not running, the
run continues on the built-in store and the trace says so once.
"""
from __future__ import annotations

import os
import re

import httpx

from app import config

DEFAULT_URL = "http://127.0.0.1:8790"
TIMEOUT = httpx.Timeout(5.0, connect=1.0)


def base_url() -> str:
    return os.getenv("METERLESS_URL", DEFAULT_URL).rstrip("/")


def enabled() -> bool:
    return config.source_enabled("meterless")


def health() -> dict:
    try:
        r = httpx.get(f"{base_url()}/health", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ConnectionError(f"Meterless service not reachable at {base_url()}: {type(exc).__name__}") from exc


class MeterlessClient:
    def __init__(self, run_id: str):
        self.namespace = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)[:120]
        self.active = enabled()
        self.error: str | None = None
        self._client = httpx.Client(timeout=TIMEOUT) if self.active else None
        if self.active:
            self._post("reset", {})

    def _post(self, op: str, body: dict):
        if not self.active:
            return None
        try:
            r = self._client.post(f"{base_url()}/v1/{self.namespace}/{op}", json={**body, "run_id": self.namespace})
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            # One failure switches the mirror off for this run; the built-in store keeps working.
            self.active = False
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            return None

    def source(self, doc):
        self._post("source", {"source_id": doc.source_id, "type": doc.type, "title": doc.title, "url": doc.url,
                              "pub_date": doc.pub_date, "access": doc.access})

    def entity(self, entity_id: str, entity_type: str, name: str):
        self._post("entity", {"entity_id": entity_id, "type": entity_type, "name": name})

    def fact(self, **fact):
        self._post("fact", fact)

    def stats(self) -> dict | None:
        if not self.active:
            return None
        try:
            r = self._client.get(f"{base_url()}/v1/{self.namespace}/stats")
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:200]
            return None
