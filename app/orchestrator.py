"""Runs the agent pipeline for one request and assembles the response."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict

from app import config
from app import graph
from app.agents import analysis
from app.agents.state import OutOfScope, RunState
from app.engines.world_model import ContextBuilderService, IngestPipelineService, WorldStoreService
from app.llm import generator
from app.tools import http
from app.trace import Tracer

AGENTS = [
    {"name": "Intake", "role": "Resolves trial and drug, picks autopsy or forecast mode, checks scope"},
    {"name": "CaseBuilder", "role": "Collects registry, genetics and literature; extracts quoted facts into the World Model"},
    {"name": "MechanismAnalyst", "role": "Decides the causal pathway and turns verified facts into calculator inputs"},
    {"name": "Calculator", "role": "Deterministic four checks: causal effect, expected HR, power, verdict"},
    {"name": "Verifier", "role": "Checks the calculator's numbers against the registry and papers with SQL run in Zetaris"},
    {"name": "Investigator", "role": "Differential diagnosis including explanations no formula covers"},
    {"name": "Critic", "role": "Checks citations and numbers, sends work back when evidence is weak"},
    {"name": "ReadThrough", "role": "Forecasts other programmes on the same target"},
    {"name": "Reporter", "role": "Writes the analyst-facing report"},
]


def run(request: dict, run_id: str | None = None) -> dict:
    run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
    tracer = Tracer(run_id)
    store = WorldStoreService(run_id)
    state = RunState(run_id=run_id, request=dict(request), tracer=tracer, store=store,
                     ingest=IngestPipelineService(store), context=ContextBuilderService(store))
    started = time.time()
    tracer.log("Orchestrator", "start_run", input_summary=str({k: v for k, v in request.items() if k != "readout_text"}),
               output_summary=f"sample_mode={config.sample_mode()}", target_agent="Intake")
    try:
        _pipeline(state)
        status = "success"
        error = None
    except OutOfScope as exc:
        status, error = "out_of_scope", {"type": "OutOfScope", "message": str(exc), "recoverable": False}
        tracer.log("Orchestrator", "out_of_scope", output_summary=str(exc), status="warning")
    except (generator.PendingModelCall, generator.PendingBatch) as exc:
        keys = [c.key for c in exc.calls] if isinstance(exc, generator.PendingBatch) else [exc.key]
        status = "error"
        error = {"type": "ModelOutputNotRecorded", "recoverable": True, "pending_calls": keys,
                 "message": "No model key configured and no recorded output for this request. Add an API key to "
                            ".env or run one of the recorded sample cases."}
        tracer.log("Orchestrator", "pending_model_calls", output_summary=f"{len(keys)} calls need a model",
                   status="error")
    except http.OfflineMiss as exc:
        status, error = "error", {"type": "OfflineCacheMiss", "message": str(exc), "recoverable": True}
        tracer.log("Orchestrator", "offline_miss", output_summary=str(exc), status="error")
    except http.SourceError as exc:
        status, error = "error", {"type": "UpstreamSourceError", "message": str(exc), "recoverable": True}
        tracer.log("Orchestrator", "source_error", output_summary=str(exc), status="error")
    except generator.ModelRefused as exc:
        status, error = "error", {"type": "ModelRefused", "message": str(exc), "recoverable": False}
        tracer.log("Orchestrator", "model_refused", output_summary=str(exc), status="error")

    _log_meterless(state)
    elapsed = round(time.time() - started, 1)
    tracer.log("Orchestrator", "finish_run", output_summary=f"status={status} in {elapsed}s")
    return _response(state, status, error, elapsed)


def _log_meterless(state: RunState):
    client = state.store.meterless
    stats = client.stats()
    state.meterless = stats
    if stats:
        state.tracer.log("Meterless", "world_model_sync",
                         output_summary=f"{stats['entities']} entities · {stats['facts']} facts · {stats['relationships']} links · "
                                        f"{stats['audit_entries']} audit entries in the Meterless World Model engine",
                         via="meterless")
    elif client.error:
        state.tracer.log("Meterless", "world_model_unavailable", output_summary=client.error, status="warning")


def _pipeline(state: RunState):
    """The agents run as a LangGraph StateGraph (app/graph.py)."""
    graph.run(state)


def _response(state: RunState, status: str, error: dict | None, elapsed: float) -> dict:
    facts = state.context.facts(state.case_id) if state.case_id else []
    body = {
        "run_id": state.run_id,
        "status": status,
        "mode": state.mode,
        "as_of": state.as_of.isoformat() if state.as_of else None,
        "backtest": state.backtest,
        "readout_date": state.trial.get("readout_date") or state.request.get("readout_date"),
        "analysis_date": (state.as_of.isoformat() if state.as_of else __import__("datetime").date.today().isoformat()),
        "trial": {k: state.trial.get(k) for k in ("nct_id", "acronym", "title", "sponsor", "status", "enrollment",
                                                  "phases", "conditions", "primary_completion")},
        "drug": state.drug,
        "agents": AGENTS,
        "trace_id": state.tracer.trace_id,
        "log_file": state.tracer.log_file,
        "execution_time_seconds": elapsed,
        "sample_mode": config.sample_mode(),
        "warnings": state.warnings,
    }
    if error:
        body["error"] = error
    if status != "success":
        return body
    body["output"] = {
        "verdict": analysis.verdict_block(state),
        "report": state.report,
        "checks": _checks(state),
        "calculator": asdict(state.calc),
        "calculator_inputs": asdict(state.case_input),
        "mechanism": state.mechanism,
        "hypotheses": state.investigation.get("hypotheses", []),
        "critic": state.critiques,
        "verification": state.verification,
        "readthrough": state.readthrough,
        "genetics": state.genetics,
        "facts": [{k: f[k] for k in ("fact_id", "entity", "key", "value", "unit", "attrs", "source_id", "quote",
                                     "pub_date", "access", "url")} for f in facts],
        "sources": [d.summary() for d in state.docs],
        "models_used": state.models_used,
        "meterless": state.meterless,
        "usage": state.usage,
    }
    return body


def _checks(state: RunState) -> list[dict]:
    c, ci = state.calc, state.case_input
    genetics = state.genetics[0] if state.genetics else {}
    from app.core import rules
    from app.core.calculator import canonical
    return [
        {"check": "Target causes disease (human genetics)", "result": ci.target_genetics,
         "detail": f"Open Targets genetic association {genetics.get('genetic_association_score')} for "
                   f"{genetics.get('target')}" if genetics else "no genetics found",
         "pass": ci.target_genetics == "causal"},
        {"check": "Drug moves a causal biomarker", "result": ci.intended_biomarker,
         "detail": f"intended biomarker {ci.intended_biomarker} is "
                   f"{'NOT causal' if canonical(ci.intended_biomarker) in rules.NON_CAUSAL_BIOMARKERS else 'causal or unknown'}; "
                   f"LDL-equivalent change {c.ldl_equivalent_mmol} mmol/L via {c.basis}",
         "pass": canonical(ci.intended_biomarker) not in rules.NON_CAUSAL_BIOMARKERS and (c.ldl_equivalent_mmol or 0) > 0.1},
        {"check": "Effect large enough", "result": c.causal_rrr,
         "detail": f"expected HR {c.expected_hr} at {ci.followup_years or 5} y (relative risk reduction "
                   f"{round((c.causal_rrr or 0) * 100, 1)}%)" if c.expected_hr else "not computable for this pathway",
         "pass": (c.causal_rrr or 0) >= rules.ROBUST_SUCCESS_RRR},
        {"check": "Trial able to detect it", "result": c.power,
         "detail": f"power {round((c.power or 0) * 100)}% with {ci.events} events; smallest detectable HR "
                   f"{c.mde_hr_80}" if c.power is not None else
                   next((f"planned events unknown; {n}" for n in c.notes if n.startswith("events needed")),
                        "planned events unknown"),
         "pass": (c.power or 0) >= 0.8},
    ]
