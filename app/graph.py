"""The agent pipeline as a LangGraph StateGraph.

Each agent is a node. The shared RunState (World Model services, documents, calculator
inputs, ...) travels in the graph state; nodes mutate it and return small updates.
The Verifier checks the calculator's numbers against the sources of record through Zetaris SQL before the
investigator and critic see them. The critic's send-back is a conditional edge: high-severity issues for the case builder or
mechanism analyst route to a refinement node and back through the analysis, at most
MAX_CRITIC_ROUNDS critic passes in total.
"""
from __future__ import annotations

import functools
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents import analysis, case_builder, intake, readthrough, verifier
from app.agents.state import RunState

MAX_CRITIC_ROUNDS = 2


class GraphState(TypedDict):
    run: RunState
    critic_round: int
    refine_requests: list[dict]


def _intake(s: GraphState):
    intake.run(s["run"])
    return {}


def _genetics(s: GraphState):
    case_builder.gather_genetics(s["run"])
    return {}


def _literature(s: GraphState):
    case_builder.gather_announcements(s["run"])
    case_builder.gather_literature(s["run"])
    case_builder.gather_genetics_literature(s["run"])
    return {}


def _extract(s: GraphState):
    case_builder.extract_all(s["run"], s["run"].docs)
    return {}


def _mechanism(s: GraphState):
    analysis.mechanism(s["run"])
    return {}


def _calculator(s: GraphState):
    analysis.calculate(s["run"])
    return {}


def _verifier(s: GraphState):
    verifier.run(s["run"])
    return {}


def _investigator(s: GraphState):
    analysis.investigate(s["run"])
    return {}


def _critic(s: GraphState):
    review = analysis.critique(s["run"])
    requests = [i for i in review.get("issues") or []
                if i.get("severity") == "high" and i.get("target_agent") in ("case_builder", "mechanism")]
    return {"critic_round": s["critic_round"] + 1, "refine_requests": requests}


def _after_critic(s: GraphState) -> str:
    from app import config
    if s["refine_requests"] and s["critic_round"] < config.max_critic_rounds():
        return "refine"
    return "accept"


def _refine(s: GraphState):
    """Critic sent work back: targeted search for the missing evidence, then re-extract."""
    run = s["run"]
    requests = s["refine_requests"]
    run.tracer.log("Orchestrator", "refinement_loop", input_summary=f"critic round {s['critic_round']}",
                   output_summary="; ".join(r.get("request", "") for r in requests)[:400],
                   target_agent="CaseBuilder", retry_count=s["critic_round"])
    terms = " OR ".join(f'"{t}"' for r in requests for t in _terms(r.get("request", "")))[:300]
    new_docs = case_builder.gather_literature(run, extra_terms=terms or None)
    if new_docs:
        case_builder.extract_all(run, new_docs)
    return {}


def _readthrough(s: GraphState):
    readthrough.run(s["run"])
    return {}


def _reporter(s: GraphState):
    analysis.report(s["run"])
    return {}


def _terms(request: str) -> list[str]:
    words = [w.strip(".,;:()") for w in request.split()]
    keep = [w for w in words if (len(w) > 3 and w[0].isupper()) or w.lower() in ("apob", "ldl", "lp(a)", "hdl")]
    return keep[:4]


@functools.lru_cache(maxsize=1)
def build():
    g = StateGraph(GraphState)
    for name, fn in [("Intake", _intake), ("Genetics", _genetics), ("Literature", _literature),
                     ("Extraction", _extract), ("MechanismAnalyst", _mechanism), ("Calculator", _calculator),
                     ("Verifier", _verifier), ("Investigator", _investigator), ("Critic", _critic), ("Refine", _refine),
                     ("ReadThrough", _readthrough), ("Reporter", _reporter)]:
        g.add_node(name, fn)
    g.add_edge(START, "Intake")
    g.add_edge("Intake", "Genetics")
    g.add_edge("Genetics", "Literature")
    g.add_edge("Literature", "Extraction")
    g.add_edge("Extraction", "MechanismAnalyst")
    g.add_edge("MechanismAnalyst", "Calculator")
    g.add_edge("Calculator", "Verifier")
    g.add_edge("Verifier", "Investigator")
    g.add_edge("Investigator", "Critic")
    g.add_conditional_edges("Critic", _after_critic, {"refine": "Refine", "accept": "ReadThrough"})
    g.add_edge("Refine", "MechanismAnalyst")
    g.add_edge("ReadThrough", "Reporter")
    g.add_edge("Reporter", END)
    return g.compile()


def run(state: RunState):
    build().invoke({"run": state, "critic_round": 0, "refine_requests": []},
                   config={"recursion_limit": 40})


def mermaid() -> str:
    return build().get_graph().draw_mermaid()
