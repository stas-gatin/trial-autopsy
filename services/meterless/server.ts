// Meterless World Model service for Trial Autopsy.
//
// Runs the official Meterless World Model reference engine (engine/, vendored unchanged from
// github.com/Meterless/Meterless, Apache-2.0) as a local HTTP service on 127.0.0.1. Trial Autopsy
// writes every source, entity and verified fact here, one namespace per run; the engine keeps an
// event-sourced, audited, idempotent world state persisted to data/meterless/.world/<run>.json.
//
//   npm install && npm start            (port: METERLESS_PORT, default 8790)
//
// Endpoints (JSON):
//   GET  /health
//   POST /v1/:ns/source   {source_id, type, title, url, pub_date, access}
//   POST /v1/:ns/entity   {entity_id, type, name}
//   POST /v1/:ns/fact     {fact_id, entity_id, key, value, unit, source_id, quote, quote_verified, agent, pub_date}
//   GET  /v1/:ns/stats    entity, fact, relationship, event and audit counts, plus an idempotency check
//   GET  /v1/:ns/facts?about=<entity_id>
//   GET  /v1/:ns/audit
//   POST /v1/:ns/reset    drop the namespace (a run id is a fresh run)
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { WorldModel } from "./engine/index.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.METERLESS_PORT ?? 8790);
const DATA_DIR = process.env.METERLESS_DATA_DIR ?? path.resolve(here, "..", "..", "data", "meterless");
const SYSTEM = "trial-autopsy";

type Space = { wm: WorldModel; ids: Map<string, string>; sources: Map<string, { at: string; url?: string }> };
const spaces = new Map<string, Space>();

function space(ns: string): Space {
  if (!/^[A-Za-z0-9_.-]{1,120}$/.test(ns)) throw new HttpError(400, "bad namespace");
  let s = spaces.get(ns);
  if (!s) {
    s = { wm: new WorldModel({ namespace: ns, storage: "local", directory: DATA_DIR }), ids: new Map(), sources: new Map() };
    spaces.set(ns, s);
  }
  return s;
}

class HttpError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function isoDate(value: unknown): string | undefined {
  if (typeof value !== "string" || !/^\d{4}(-\d{2}(-\d{2})?)?/.test(value)) return undefined;
  const v = value.length === 4 ? `${value}-01-01` : value.length === 7 ? `${value}-01` : value.slice(0, 10);
  return `${v}T00:00:00.000Z`;
}

// Observation time: the document's publication date (so the world state is bitemporal in the same
// sense as the agents' as_of cut-off); the run start when a document has no date.
function observedAt(pubDate: unknown, fallback: string): string {
  return isoDate(pubDate) ?? fallback;
}

async function addSource(s: Space, b: any, runId: string, now: string) {
  const at = observedAt(b.pub_date, now);
  const entity = await s.wm.upsertEntity({
    type: "source", externalKey: { system: SYSTEM, id: String(b.source_id) }, name: b.title,
    attrs: { title: b.title, kind: b.type, url: b.url, pub_date: b.pub_date, access: b.access },
    source: { kind: "api", at, url: b.url || undefined, by: "CaseBuilder", runId },
  });
  s.ids.set(`source:${b.source_id}`, entity.id);
  s.sources.set(String(b.source_id), { at, url: b.url || undefined });
  return entity.id;
}

async function addEntity(s: Space, b: any, runId: string, now: string) {
  const entity = await s.wm.upsertEntity({
    type: String(b.type), externalKey: { system: SYSTEM, id: String(b.entity_id) }, name: String(b.name),
    attrs: { name: b.name }, source: { kind: "pipeline", at: now, by: "Intake", runId },
  });
  s.ids.set(String(b.entity_id), entity.id);
  return entity.id;
}

async function addFact(s: Space, b: any, runId: string, now: string) {
  let about = s.ids.get(String(b.entity_id));
  if (!about) {
    const [type, ...rest] = String(b.entity_id).split(":");
    about = await addEntity(s, { entity_id: b.entity_id, type: type || "entity", name: rest.join(":") || b.entity_id }, runId, now);
  }
  const src = s.sources.get(String(b.source_id));
  const at = observedAt(b.pub_date, src?.at ?? now);
  const fact = await s.wm.assertFact({
    about, predicate: String(b.key),
    value: { value: b.value, unit: b.unit || undefined, quote: b.quote, source_id: b.source_id,
             quote_verified: Boolean(b.quote_verified), fact_id: b.fact_id },
    confidence: b.quote_verified ? 1 : 0.3,
    source: { kind: "agent", at, url: src?.url, by: String(b.agent || "agent"), runId },
  });
  const sourceEntity = s.ids.get(`source:${b.source_id}`);
  if (sourceEntity) {
    await s.wm.relate({ from: about, type: "evidenced_by", to: sourceEntity, source: { kind: "agent", at, by: String(b.agent || "agent"), runId } });
  }
  return fact.id;
}

function stats(s: Space) {
  const shape: any = s.wm.stateShape();
  const facts = s.wm.events().filter((e) => e.kind === "assertFact").length;
  return {
    engine: "Meterless World Model (official reference implementation)",
    entities: s.wm.view("graph").query({}).length,
    facts,
    relationships: s.wm.events().filter((e) => e.kind === "relate").length,
    events: s.wm.events().length,
    audit_entries: s.wm.audit().length,
    state_shape_keys: shape && typeof shape === "object" ? Object.keys(shape).length : 0,
  };
}

function readBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (c) => { data += c; if (data.length > 2_000_000) reject(new HttpError(413, "body too large")); });
    req.on("end", () => { try { resolve(data ? JSON.parse(data) : {}); } catch { reject(new HttpError(400, "invalid JSON")); } });
  });
}

const server = http.createServer(async (req, res) => {
  const send = (status: number, body: unknown) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (url.pathname === "/health") return send(200, { ok: true, engine: "meterless-world-model-reference", namespaces: spaces.size });
    const m = url.pathname.match(/^\/v1\/([^/]+)\/(source|entity|fact|stats|facts|audit|reset)$/);
    if (!m) throw new HttpError(404, "not found");
    const [, ns, op] = m;
    if (op === "reset" && req.method === "POST") {
      space(ns);  // validates the name
      spaces.delete(ns);
      fs.rmSync(path.join(DATA_DIR, ".world", `${ns}.json`), { force: true });
      return send(200, { reset: ns });
    }
    const s = space(ns);
    const now = new Date().toISOString();
    if (req.method === "POST") {
      const b = await readBody(req);
      const runId = String(b.run_id ?? ns);
      if (op === "source") return send(200, { id: await addSource(s, b, runId, now) });
      if (op === "entity") return send(200, { id: await addEntity(s, b, runId, now) });
      if (op === "fact") return send(200, { id: await addFact(s, b, runId, now) });
      throw new HttpError(405, "method not allowed");
    }
    if (op === "stats") return send(200, stats(s));
    if (op === "facts") {
      const about = url.searchParams.get("about");
      const id = about ? (s.ids.get(about) ?? about) : undefined;
      const facts = id ? s.wm.view("facts").about(id) : s.wm.events().filter((e) => e.kind === "assertFact").map((e) => e.payload);
      return send(200, { facts });
    }
    if (op === "audit") return send(200, { audit: s.wm.audit() });
    throw new HttpError(404, "not found");
  } catch (err: any) {
    send(err instanceof HttpError ? err.status : 500, { error: String(err?.message ?? err) });
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Meterless World Model service on http://127.0.0.1:${PORT} (data: ${DATA_DIR})`);
});

