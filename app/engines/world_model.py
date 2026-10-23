"""Meterless World Model engine, minimal headless implementation.

Three services from the Meterless reference architecture:
  WorldStoreService      - durable SQLite store of sources, entities, facts, relationships
  IngestPipelineService  - idempotent, ordered write path (validate -> normalize -> dedupe ->
                           resolve entity -> write -> link -> audit)
  ContextBuilderService  - structured context payloads for agents, with a manifest of what
                           was injected

Every write emits a JSONL audit record (logs/world-<run_id>.jsonl) so a judge can see
which agent asserted which fact, from which source, with which quote.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone

from app import config
from app.config import WORLD_DB_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, type TEXT, title TEXT, url TEXT, pub_date TEXT, access TEXT, meta TEXT);
CREATE TABLE IF NOT EXISTS entities (
  entity_id TEXT PRIMARY KEY, type TEXT, name TEXT, props TEXT);
CREATE TABLE IF NOT EXISTS facts (
  fact_id TEXT PRIMARY KEY, run_id TEXT, case_id TEXT, entity_id TEXT, key TEXT, value TEXT, unit TEXT,
  attrs TEXT, source_id TEXT, quote TEXT, quote_verified INTEGER, agent TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS relationships (
  src TEXT, rel TEXT, dst TEXT, run_id TEXT, PRIMARY KEY (src, rel, dst, run_id));
CREATE INDEX IF NOT EXISTS facts_case ON facts(run_id, case_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fact_id(case_id: str, entity_id: str, key: str, value, source_id: str, quote: str) -> str:
    blob = json.dumps([case_id, entity_id, key, value, source_id, quote], sort_keys=True, default=str)
    return "f_" + hashlib.sha256(blob.encode()).hexdigest()[:14]


class WorldStoreService:
    def __init__(self, run_id: str, db_name: str = "world.db"):
        WORLD_DB_DIR.mkdir(parents=True, exist_ok=True)
        config.logs_dir().mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.db = sqlite3.connect(WORLD_DB_DIR / db_name, check_same_thread=False, timeout=60)
        self.db.executescript(SCHEMA)
        # A run id is a fresh run: drop anything a previous run with the same id left behind.
        self.db.execute("DELETE FROM facts WHERE run_id=?", (run_id,))
        self.db.execute("DELETE FROM relationships WHERE run_id=?", (run_id,))
        self.db.commit()
        self.audit_path = config.logs_dir() / f"world-{run_id}.jsonl"
        self.audit_path.write_text("")
        self._lock = threading.Lock()
        from app.engines.meterless import MeterlessClient
        self.meterless = MeterlessClient(run_id)  # the official Meterless engine, run as a local service

    def audit(self, op: str, payload: dict):
        with self._lock, self.audit_path.open("a") as f:
            f.write(json.dumps({"ts": _now(), "run_id": self.run_id, "op": op, **payload},
                               ensure_ascii=False, default=str) + "\n")

    def execute(self, sql: str, params=()):
        with self._lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def query(self, sql: str, params=()) -> list[tuple]:
        with self._lock:
            return self.db.execute(sql, params).fetchall()


class IngestPipelineService:
    def __init__(self, store: WorldStoreService):
        self.store = store

    def upsert_source(self, doc) -> str:
        self.store.execute(
            "INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?,?)",
            (doc.source_id, doc.type, doc.title, doc.url, doc.pub_date, doc.access,
             json.dumps({k: v for k, v in doc.meta.items() if k != "summary"}, default=str)))
        self.store.meterless.source(doc)
        return doc.source_id

    def upsert_entity(self, entity_type: str, name: str, props: dict | None = None) -> str:
        entity_id = f"{entity_type}:{name.strip().lower()}"
        self.store.execute("INSERT OR IGNORE INTO entities VALUES (?,?,?,?)",
                           (entity_id, entity_type, name, json.dumps(props or {}, default=str)))
        self.store.meterless.entity(entity_id, entity_type, name)
        return entity_id

    def ingest_fact(self, *, case_id: str, entity_id: str, key: str, value, unit: str = "", attrs: dict | None = None,
                    source_id: str, quote: str, quote_verified: bool, agent: str) -> tuple[str, bool]:
        """Canonical, idempotent write. Returns (fact_id, created)."""
        if not key or source_id is None:
            raise ValueError("fact needs a key and a source")
        normalized = value if isinstance(value, (int, float, str, type(None))) else json.dumps(value, default=str)
        fid = fact_id(case_id, entity_id, key, normalized, source_id, quote)
        exists = self.store.query("SELECT 1 FROM facts WHERE fact_id=? AND run_id=?", (fid, self.store.run_id))
        if exists:
            return fid, False
        self.store.execute(
            "INSERT OR REPLACE INTO facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, self.store.run_id, case_id, entity_id, key, json.dumps(normalized), unit,
             json.dumps(attrs or {}, default=str), source_id, quote, int(quote_verified), agent, _now()))
        self.store.execute("INSERT OR IGNORE INTO relationships VALUES (?,?,?,?)",
                           (entity_id, "asserted_by", source_id, self.store.run_id))
        self.store.audit("ingest_fact", {"fact_id": fid, "case_id": case_id, "entity": entity_id, "key": key,
                                         "value": normalized, "unit": unit, "source_id": source_id,
                                         "quote_verified": quote_verified, "agent": agent})
        pub = self.store.query("SELECT pub_date FROM sources WHERE source_id=?", (source_id,))
        self.store.meterless.fact(fact_id=fid, entity_id=entity_id, key=key, value=normalized, unit=unit,
                                  source_id=source_id, quote=quote, quote_verified=quote_verified, agent=agent,
                                  pub_date=pub[0][0] if pub else None)
        return fid, True


class ContextBuilderService:
    def __init__(self, store: WorldStoreService):
        self.store = store

    def facts(self, case_id: str, verified_only: bool = True) -> list[dict]:
        rows = self.store.query(
            "SELECT f.fact_id, f.entity_id, f.key, f.value, f.unit, f.attrs, f.source_id, f.quote, f.quote_verified, "
            "f.agent, s.title, s.pub_date, s.access, s.url FROM facts f LEFT JOIN sources s ON f.source_id=s.source_id "
            "WHERE f.run_id=? AND f.case_id=?" + (" AND f.quote_verified=1" if verified_only else "") +
            " ORDER BY f.entity_id, f.key, f.fact_id", (self.store.run_id, case_id))
        return [{"fact_id": r[0], "entity": r[1], "key": r[2], "value": json.loads(r[3]), "unit": r[4],
                 "attrs": json.loads(r[5]), "source_id": r[6], "quote": r[7], "quote_verified": bool(r[8]),
                 "agent": r[9], "source_title": r[10], "pub_date": r[11], "access": r[12], "url": r[13]}
                for r in rows]

    def build(self, case_id: str, for_agent: str, keys: set[str] | None = None) -> dict:
        facts = [f for f in self.facts(case_id) if keys is None or f["key"] in keys]
        payload = {
            "case_id": case_id,
            "facts": [{"fact_id": f["fact_id"], "entity": f["entity"], "key": f["key"], "value": f["value"],
                       "unit": f["unit"], "attrs": f["attrs"], "source": f["source_id"],
                       "source_date": f["pub_date"], "quote": f["quote"][:300]} for f in facts],
        }
        manifest = {"for_agent": for_agent, "fact_ids": [f["fact_id"] for f in facts], "count": len(facts)}
        self.store.audit("build_context", manifest)
        return payload
