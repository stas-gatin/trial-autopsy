"""Shared run state passed between agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.core.calculator import CaseInput, Result
from app.engines.world_model import ContextBuilderService, IngestPipelineService, WorldStoreService
from app.tools.documents import Document
from app.trace import Tracer


class OutOfScope(Exception):
    pass


@dataclass
class RunState:
    run_id: str
    request: dict
    tracer: Tracer
    store: WorldStoreService
    ingest: IngestPipelineService
    context: ContextBuilderService
    case_id: str = ""
    mode: str = "auto"
    as_of: date | None = None
    backtest: bool = False
    study: dict | None = None
    trial: dict = field(default_factory=dict)
    drug: dict = field(default_factory=dict)
    docs: list[Document] = field(default_factory=list)
    genetics: list[dict] = field(default_factory=list)
    mechanism: dict = field(default_factory=dict)
    case_input: CaseInput | None = None
    calc: Result | None = None
    investigation: dict = field(default_factory=dict)
    critiques: list[dict] = field(default_factory=list)
    auto_issues: list[str] = field(default_factory=list)
    readthrough: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    models_used: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)   # agent -> calls, tokens, model (for the cost panel)
    meterless: dict | None = None               # Meterless World Model engine stats for this run
    verification: dict = field(default_factory=dict)   # Verifier: the calculator's numbers checked through Zetaris

    def record_model(self, agent: str, response) -> None:
        self.models_used[agent] = f"{response.provider}:{response.model}"
        u = self.usage.setdefault(agent, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated": False,
                                          "model": response.model, "provider": response.provider})
        u["calls"] += 1
        u["input_tokens"] += response.input_tokens
        u["output_tokens"] += response.output_tokens
        u["estimated"] = u["estimated"] or response.tokens_estimated

    @property
    def trial_label(self) -> str:
        t = self.trial
        return f"{t.get('acronym') or ''} {t.get('nct_id') or ''} - {t.get('title') or ''}".strip(" -")

    @property
    def doc_index(self) -> dict[str, Document]:
        return {d.source_id: d for d in self.docs}
