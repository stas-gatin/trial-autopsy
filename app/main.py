"""HTTP API and web UI.

POST /run                 synchronous run (judges, scripts)
POST /api/runs            start a run in the background (web UI), returns run_id
GET  /api/runs/{run_id}   status and result
GET  /api/runs/{run_id}/trace   live trace events
GET  /api/examples        recorded sample inputs
GET  /health
"""
from __future__ import annotations

import json
import re
import threading
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, orchestrator

app = FastAPI(title="Trial Autopsy", version="0.1.0")
WEB = config.ROOT / "app" / "web"
RUNS = config.DATA / "runs"
LIBRARY = config.DATA / "library"
_jobs: dict[str, dict] = {}


class RunInput(BaseModel):
    trial: str | None = Field(None, description="ClinicalTrials.gov NCT id, e.g. NCT04023552")
    drug: str | None = None
    mode: str = Field("auto", description="auto | autopsy | forecast")
    readout_text: str | None = Field(None, description="Topline press release text, if any")
    readout_date: str | None = None
    as_of: str | None = Field(None, description="Evidence cut-off date (YYYY-MM-DD)")
    archive_case: str | None = Field(None, description="Backtest on a frozen archive (data/archive/<case>)")


class RunOptions(BaseModel):
    sample_mode: bool | None = None


class RunRequest(BaseModel):
    run_id: str | None = None
    input: RunInput
    options: RunOptions = RunOptions()


def _execute(req: RunRequest, run_id: str) -> dict:
    if req.options.sample_mode is not None:
        import os
        os.environ["SAMPLE_MODE"] = "true" if req.options.sample_mode else "false"
    result = orchestrator.run(req.input.model_dump(exclude_none=True), run_id=run_id)
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{run_id}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return result


@app.get("/health")
def health():
    return {"status": "ok", "sample_mode": config.sample_mode()}


@app.post("/run")
def run_sync(req: RunRequest):
    run_id = req.run_id or f"run-{uuid.uuid4().hex[:8]}"
    return JSONResponse(_execute(req, run_id))


@app.post("/api/runs")
def run_async(req: RunRequest):
    run_id = req.run_id or f"run-{uuid.uuid4().hex[:8]}"
    _jobs[run_id] = {"status": "running"}

    def work():
        try:
            _jobs[run_id] = {"status": "done", "result": _execute(req, run_id)}
        except Exception as exc:  # surfaced to the UI, never swallowed
            _jobs[run_id] = {"status": "done", "result": {"run_id": run_id, "status": "error",
                                                          "error": {"type": type(exc).__name__, "message": str(exc),
                                                                    "recoverable": False}}}

    threading.Thread(target=work, daemon=True).start()
    return {"run_id": run_id, "status": "running"}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    if run_id in _jobs:
        return _jobs[run_id]
    for folder in (RUNS, LIBRARY):
        path = folder / f"{run_id}.json"
        if path.exists():
            return {"status": "done", "result": json.loads(path.read_text())}
    raise HTTPException(404, "unknown run")


@app.get("/api/runs/{run_id}/timeline")
def run_timeline(run_id: str):
    """Time machine: documents the agents could read (published before as_of) and documents that
    appeared later and were hidden from them (post-cutoff genetics, and for historical backtests the
    answer key's sources such as the full results paper)."""
    result = None
    for folder in (RUNS, LIBRARY):
        path = folder / f"{run_id}.json"
        if path.exists():
            result = json.loads(path.read_text())
            break
    if not result:
        raise HTTPException(404, "unknown run")
    out, as_of = result.get("output") or {}, result.get("as_of")
    seen = [{"title": d.get("title"), "date": d.get("pub_date"), "type": d.get("type"), "url": d.get("url")}
            for d in out.get("sources") or [] if d.get("pub_date")]
    hidden = []
    if as_of:
        for g in out.get("genetics") or []:
            for e in g.get("top_evidence") or []:
                if e.get("publication_date") and e["publication_date"] > as_of:
                    hidden.append({"title": f"{g.get('target')} genetics: {e.get('trait')} GWAS ({e.get('study_id')}, n={e.get('n')})",
                                   "date": e["publication_date"], "type": "genetics",
                                   "url": f"https://pubmed.ncbi.nlm.nih.gov/{e['pmid']}/" if e.get("pmid") else None})
        if result.get("backtest") and run_id.startswith("bt-"):
            gold = config.DATA / "golden_set" / f"{run_id[3:]}.json"
            if gold.exists():
                for src in json.loads(gold.read_text()).get("sources") or []:
                    if src.get("pub_date") and src["pub_date"] > as_of:
                        hidden.append({"title": src.get("title"), "date": src["pub_date"], "type": src.get("type"), "url": src.get("url")})
    seen_titles = {(d["title"] or "").lower()[:60] for d in seen}
    hidden = [h for h in hidden if (h["title"] or "").lower()[:60] not in seen_titles]
    return {"as_of": as_of, "readout_date": (result.get("trial") or {}).get("readout_date"), "seen": seen,
            "hidden": sorted(hidden, key=lambda h: h["date"])}


@app.get("/api/runs/{run_id}/trace")
def run_trace(run_id: str):
    path = config.LOGS / f"trace-{run_id}.jsonl"
    if not path.exists():
        path = config.DATA / "library" / "traces" / f"trace-{run_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _short_title(title: str | None) -> str | None:
    if not title:
        return None
    return title if len(title) <= 48 else title[:46].rsplit(" ", 1)[0] + "…"


def _known_names() -> dict:
    """Short trial names by NCT ID (registry titles can be long, and some trials have no registry acronym)."""
    path = config.DATA / "reference" / "trial_ids.json"
    if not path.exists():
        return {}
    return {t["nct"]: t["acronym"] for g in json.loads(path.read_text())["groups"] for t in g["trials"]}


KNOWN_NAMES = _known_names()


def _summary(result: dict, origin: str) -> dict:
    out, trial = result.get("output") or {}, result.get("trial") or {}
    verdict, calc = out.get("verdict") or {}, out.get("calculator") or {}
    kind = "backtest" if result.get("backtest") else result.get("mode")
    return {
        "run_id": result.get("run_id"), "origin": origin, "kind": kind, "mode": result.get("mode"),
        "trial": KNOWN_NAMES.get(trial.get("nct_id")) or trial.get("acronym") or _short_title(trial.get("title")) or trial.get("nct_id"),
        "nct_id": trial.get("nct_id"),
        "sponsor": trial.get("sponsor"), "drug": (result.get("drug") or {}).get("name"),
        "mechanism": "; ".join((result.get("drug") or {}).get("mechanisms") or []),
        "outcome": verdict.get("outcome"), "primary_category": verdict.get("primary_category"),
        "confidence": verdict.get("confidence"), "probability_of_success": verdict.get("probability_of_success"),
        "expected_hr": calc.get("expected_hr"), "headline": (out.get("report") or {}).get("headline"),
        "analysis_date": result.get("analysis_date") or result.get("as_of"), "readout_date": result.get("readout_date"),
        "target": (((result.get("drug") or {}).get("targets") or [{}])[0] or {}).get("symbol"),
        "target_genetics": (out.get("calculator_inputs") or {}).get("target_genetics"),
        "intended_biomarker": (out.get("calculator_inputs") or {}).get("intended_biomarker"),
        "enrollment": trial.get("enrollment"),
    }


@app.get("/api/analyses")
def analyses():
    """Every finished analysis: the committed library plus runs made on this machine."""
    seen, items = set(), []
    for folder, origin in ((RUNS, "recent"), (LIBRARY, "library")):
        for path in sorted(folder.glob("*.json")) if folder.exists() else []:
            try:
                result = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if result.get("status") != "success" or result.get("run_id") in seen:
                continue
            seen.add(result.get("run_id"))
            summary = _summary(result, origin)
            same = (summary["nct_id"], summary["kind"], summary["analysis_date"])
            if same in seen:      # e.g. the ACCELERATE demo and its backtest are the same analysis
                continue
            seen.add(same)
            items.append(summary)
    order = {"autopsy": 0, "forecast": 1, "backtest": 2}
    items.sort(key=lambda x: (x["origin"] != "recent" or x["run_id"].startswith(("example-", "bt-")),
                              order.get(x["kind"], 3), x["trial"] or ""))
    return items


def _backtest_files(label: str = "") -> list:
    """Scored backtests, oldest first. No label: the product's own runs; a label (e.g. "claude"): a comparison run."""
    pattern = re.compile(rf"backtest_\d{{4}}-\d{{2}}-\d{{2}}{'_' + re.escape(label) if label else ''}\.json$")
    return sorted(p for p in (config.ROOT / "eval" / "results").glob("backtest_*.json") if pattern.match(p.name))


@app.get("/api/accuracy")
def accuracy():
    """Latest blind-backtest score (eval/run_backtests.py) and the calculator's development-set score."""
    results = _backtest_files()
    backtest = json.loads(results[-1].read_text())["summary"] if results else None
    calc_files = sorted((config.ROOT / "eval" / "results").glob("calculator_*.json"))
    calc = None
    if calc_files:
        rows = json.loads(calc_files[-1].read_text())
        scored = [r for r in rows if r.get("outcome_match") is not None]
        calc = {"outcome_correct": sum(r["outcome_match"] for r in scored), "outcome_scored": len(scored)}
    return {"backtest": backtest, "calculator": calc}


@app.get("/api/trial-ids")
def trial_ids():
    """Verified trial IDs for the 'Where do I find an ID?' help, with the finished analysis (if any) for each."""
    data = json.loads((config.DATA / "reference" / "trial_ids.json").read_text())
    recorded = {}
    for path in sorted(LIBRARY.glob("*.json")):
        result = json.loads(path.read_text())
        nct = (result.get("trial") or {}).get("nct_id")
        if nct and result.get("status") == "success":
            # prefer a historical backtest, then a demo example
            if nct not in recorded or path.stem.startswith("bt-"):
                recorded[nct] = path.stem
    for g in data["groups"]:
        for t in g["trials"]:
            t["run_id"] = recorded.get(t["nct"])
    return data


@app.get("/api/models")
def models():
    """List prices per model and the recommended model for each AI agent (for the cost panel)."""
    from app import settings
    agents = {a["name"]: a.get("recommended") for a in config.AGENTS if a.get("kind") == "ai"}
    return {"models": settings.MODEL_INFO, "recommended": agents}


@app.get("/api/predictions")
def predictions():
    """Pre-registered, falsifiable predictions (eval/predictions/index.json), committed before the results."""
    path = config.ROOT / "eval" / "predictions" / "index.json"
    return {"predictions": json.loads(path.read_text()) if path.exists() else []}


@app.get("/api/calibration")
def calibration():
    """Per-trial blind-backtest rows: what the agents predicted before the result vs what the trial found.
    The observed hazard ratios come from the held-out answer key (data/golden_set), never seen by the agents."""
    results = _backtest_files()
    if not results:
        return {"rows": []}
    rows = []
    for r in json.loads(results[-1].read_text())["rows"]:
        gold_path = config.DATA / "golden_set" / f"{r['case']}.json"
        gold = json.loads(gold_path.read_text()) if gold_path.exists() else {}
        out = gold.get("outcome") or {}
        val = lambda k: (out.get(k) or {}).get("value") if isinstance(out.get(k), dict) else out.get(k)
        name = (gold.get("meta") or {}).get("trial_name") or r["case"]
        rows.append({
            "case": r["case"], "run_id": f"bt-{r['case']}", "trial": name.split(" (")[0],
            "predicted_hr": r.get("agent_expected_hr"), "chance": r.get("agent_power"),
            "predicted": r.get("predicted"), "actual": r.get("actual"),
            "observed_hr": val("hr"), "ci": [val("ci_low"), val("ci_high")],
            "cause": r.get("final_category"), "gold_cause": r.get("gold_category"),
            "cause_correct": r.get("category_correct"), "cause_overlap": r.get("category_overlap"),
            "prediction_correct": r.get("prediction_correct"),
        })
    return {"rows": rows}


@app.get("/api/benchmark")
def benchmark():
    """The same 14 blind backtests scored for the product's models (NVIDIA Nemotron 3) and for Claude, with the
    average model time and tokens per analysis from the runs themselves."""
    def runs_stats(folder):
        times, tokens = [], []
        for path in sorted(folder.glob("bt-*.json")):
            run = json.loads(path.read_text())
            if run.get("status") != "success":
                continue
            usage = (run.get("output") or {}).get("usage") or {}
            tokens.append(sum(u.get("input_tokens", 0) + u.get("output_tokens", 0) for u in usage.values()))
            if run.get("execution_time_seconds", 0) > 5:      # replays take a fraction of a second
                times.append(run["execution_time_seconds"])
        avg = lambda xs: round(sum(xs) / len(xs)) if xs else None
        return {"avg_tokens": avg(tokens), "avg_seconds": avg(times)}

    out = []
    for label, name, folder in (("", "NVIDIA Nemotron", LIBRARY),
                                ("claude", "Claude Opus 5.5", config.ROOT / "eval" / "benchmark" / "claude-opus-5-5")):
        files = _backtest_files(label)
        if not files:
            continue
        data = json.loads(files[-1].read_text())
        out.append({"label": label or "product", "name": name, "summary": data["summary"],
                    "rows": {r["case"]: {k: r.get(k) for k in ("predicted", "actual", "prediction_correct", "agent_expected_hr",
                                                                 "key_expected_hr", "final_category", "gold_category",
                                                                 "category_correct", "category_overlap", "models")}
                             for r in data["rows"]},
                    **runs_stats(folder)})
    return {"models": out}


class SettingsUpdate(BaseModel):
    sources: dict[str, bool] | None = None
    providers: dict[str, dict] | None = None
    roles: dict[str, dict] | None = None
    agents: dict[str, dict] | None = None
    apply_recommended: bool | None = None
    run: dict[str, object] | None = None
    zetaris: dict[str, object] | None = None   # url, org, key, clear_key: without it the Zetaris card's changes were dropped


@app.get("/api/settings")
def get_settings():
    from app import settings
    return settings.read()


@app.post("/api/settings")
def save_settings(update: SettingsUpdate):
    from app import settings
    try:
        return settings.update(update.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/settings/test/provider/{provider}")
def test_provider(provider: str, model: str | None = None):
    from app import settings
    return settings.test_provider(provider, model)


@app.post("/api/settings/meterless/detect")
def meterless_detect():
    from app import settings
    return settings.meterless_detect()


@app.post("/api/settings/test/source/{source}")
def test_source(source: str):
    from app import settings
    return settings.test_source(source)


@app.get("/api/target")
def target(symbol: str, ensembl: str, drugs: str = ""):
    """Plain-language profile of the target protein and which 3D structure to show."""
    from app.tools import http, structures
    if not re.fullmatch(r"ENSG\d{11}", ensembl):
        raise HTTPException(400, "invalid Ensembl id")
    try:
        return structures.target_profile(symbol, ensembl, [d.strip() for d in drugs.split(",") if d.strip()][:4])
    except (http.SourceError, http.OfflineMiss) as exc:
        return {"symbol": symbol, "available": False, "reason": str(exc)}


@app.get("/api/structure")
def structure(source: str, id: str, url: str | None = None):
    """Structure file proxied through the server (cached, works offline for recorded cases)."""
    from fastapi.responses import PlainTextResponse
    from app.tools import http, structures
    if source == "pdb" and re.fullmatch(r"[0-9a-zA-Z]{4}", id):
        profile = {"source": "pdb", "id": id}
    elif source == "alphafold" and url and re.fullmatch(r"https://alphafold\.ebi\.ac\.uk/files/AF-[A-Z0-9]+-F\d+-model_v\d+\.pdb", url):
        profile = {"source": "alphafold", "id": id, "url": url}
    else:
        raise HTTPException(400, "invalid structure request")
    try:
        return PlainTextResponse(structures.structure_file(profile) or "")
    except (http.SourceError, http.OfflineMiss) as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/graph")
def agent_graph():
    """Mermaid source of the LangGraph agent graph."""
    from app import graph
    return {"mermaid": graph.mermaid()}


@app.get("/api/examples")
def examples():
    out = []
    for path in sorted((config.ROOT / "input_examples").glob("*.json")):
        data = json.loads(path.read_text())
        out.append({"file": path.name, "label": data.get("label", path.stem), "request": data})
    return out


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Always revalidate the UI so a judge never sees an old cached page after an update."""
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
