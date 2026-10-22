"""Runtime configuration from environment variables (.env supported)."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
LOGS = ROOT / "logs"
HTTP_CACHE = DATA / "cache" / "http"
LLM_CACHE = DATA / "llm_cache"
ARCHIVE = DATA / "archive"
WORLD_DB_DIR = DATA / "world"


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Role:
    provider: str
    model: str


# The product runs on NVIDIA Nemotron (open models, NVIDIA API): Nemotron 3 Ultra for reasoning, Nemotron 3.5 Super
# for the rest (NVIDIA retired the Nemotron 3 Super endpoint in October 2026). Claude is an alternative per agent.
# Meterless Nightshift is the primary provider: an agent asks for a routing mode and Nightshift picks the Nemotron lane
# that answers. Without Nightshift a call goes straight to NVIDIA's API (Ultra for deep, 3.5 Super for fast); without
# either, recorded answers are replayed.
ROLE_DEFAULTS = {
    "reasoning": "meterless:meterless/deep",
    "extraction": "meterless:meterless/fast",
    "critic": "meterless:meterless/fast",
    "reporter": "meterless:meterless/fast",
}


def sample_mode() -> bool:
    return _flag("SAMPLE_MODE")


def role(name: str) -> Role:
    spec = os.getenv(f"ROLE_{name.upper()}", ROLE_DEFAULTS[name])
    provider, _, model = spec.partition(":")
    return Role(provider.strip(), model.strip())


# Every agent in graph order. AI agents belong to a role group (used for recorded-output keys and defaults);
# each can be given its own provider:model on the Settings page (env AGENT_<NAME>).
AGENTS = [
    {"name": "Intake", "kind": "code", "does": "Resolves the trial and drug, picks autopsy or forecast, checks scope"},
    {"name": "Genetics", "kind": "code", "does": "Fetches target-disease genetics from Open Targets"},
    {"name": "Literature", "kind": "code", "does": "Finds trial papers and pre-cut-off genetics papers (Europe PMC) or the frozen archive"},
    {"name": "Extraction", "kind": "ai", "group": "extraction", "tier": "fast",
     "recommended": "meterless:meterless/fast", "alternative": "anthropic:claude-haiku-4-5",
     "does": "Reads each document and copies out numbers with verbatim quotes",
     "why": "Many calls per case and a narrow task. Every quote is verified against the source by code, so a fast, cheap model is enough."},
    {"name": "MechanismAnalyst", "kind": "ai", "group": "reasoning", "tier": "top",
     "recommended": "meterless:meterless/deep", "alternative": "anthropic:claude-opus-5-5",
     "does": "Decides the causal pathway and chooses which numbers the calculator gets",
     "why": "The weakest link in the blind backtests: picking the right number (e.g. the on-statin effect, not a healthy-volunteer one) decides the verdict. Use the most capable model: Nemotron 3 Ultra, reasoning step by step."},
    {"name": "Calculator", "kind": "code", "does": "Four deterministic checks: effect size, expected HR, power, verdict"},
    {"name": "Verifier", "kind": "ai", "group": "critic", "tier": "strong",
     "recommended": "meterless:meterless/fast", "alternative": "anthropic:claude-sonnet-5-5",
     "does": "Checks the calculator's numbers against the registry and the papers by writing SQL for Zetaris",
     "why": "An independent second check of the numbers the verdict rests on: Nemotron writes the Lightning SQL, Zetaris "
            "runs it over the sources of record, and a contradiction goes to the critic. Without Zetaris it cannot run."},
    {"name": "Investigator", "kind": "ai", "group": "reasoning", "tier": "top",
     "recommended": "meterless:meterless/deep", "alternative": "anthropic:claude-opus-5-5",
     "does": "Weighs every possible explanation with evidence for and against",
     "why": "Long, careful reasoning over many facts without overclaiming. Use the most capable model: Nemotron 3 Ultra, reasoning step by step."},
    {"name": "Critic", "kind": "ai", "group": "critic", "tier": "strong",
     "recommended": "meterless:meterless/fast", "alternative": "anthropic:claude-sonnet-5-5",
     "does": "Checks inputs and claims; can send the analysis back for more evidence",
     "why": "A different model from the analyst (Nemotron 3.5 Super, not Ultra) with its own prompt catches errors the analyst's model would repeat. Strong but independent."},
    {"name": "Refine", "kind": "code", "does": "Targeted search for evidence the critic asked for"},
    {"name": "ReadThrough", "kind": "ai", "group": "extraction", "tier": "fast",
     "recommended": "meterless:meterless/fast", "alternative": "anthropic:claude-haiku-4-5",
     "does": "Finds rival drugs on the same target and reads their phase 2 results",
     "why": "Short abstract extraction, verified by code; the forecast itself is computed by the calculator."},
    {"name": "Reporter", "kind": "ai", "group": "reporter", "tier": "mid",
     "recommended": "meterless:meterless/fast", "alternative": "anthropic:claude-sonnet-5-5",
     "does": "Writes the analyst-facing report from the finished analysis",
     "why": "Clear writing from material already verified; it may not add numbers. A mid-tier model writes well and costs less."},
]


def agent_spec(agent: str | None, group: str) -> Role:
    """Model for one agent: its own AGENT_<NAME> setting, else its role group's setting."""
    if agent:
        spec = os.getenv(f"AGENT_{agent.upper()}")
        if spec and ":" in spec:
            provider, _, model = spec.partition(":")
            return Role(provider.strip(), model.strip())
    return role(group)


def provider_key(provider: str) -> str | None:
    if provider == "meterless":
        from app.llm import meterless_runtime
        return meterless_runtime.token()
    key = {
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "nvidia": os.getenv("NVIDIA_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
    }.get(provider)
    if not key or key.startswith("your-"):
        return None
    return key


NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


def base_url(provider: str) -> str | None:
    """API address for an OpenAI-compatible provider. NVIDIA always has one: without it the OpenAI client
    would send the NVIDIA key to api.openai.com."""
    if provider == "nvidia":
        return (os.getenv("NVIDIA_BASE_URL") or "").strip() or NVIDIA_DEFAULT_BASE_URL
    if provider == "meterless":
        from app.llm import meterless_runtime
        return meterless_runtime.base_url()
    if provider == "openai":
        return (os.getenv("OPENAI_BASE_URL") or "").strip() or None
    return None


def contact_email() -> str:
    return os.getenv("CONTACT_EMAIL", "trial-autopsy@example.com")


# --- Data sources and run options (editable on the Settings page) -----------------------------------

SOURCES = {
    "ctgov": {"label": "ClinicalTrials.gov", "required": True,
              "role": "Trial registry: design, size, endpoints, status, posted results"},
    "opentargets": {"label": "Open Targets Platform", "required": False,
                    "role": "Human genetics linking the drug's target to coronary disease; drug mechanisms; rival programmes"},
    "europepmc": {"label": "Europe PMC", "required": False,
                  "role": "Literature: phase 2 results, trial design papers, open full texts (with a date filter)"},
    "genetics_literature": {"label": "Genetics literature search", "required": False,
                            "role": "Pre-cut-off genetic association papers on the target (Europe PMC)"},
    "sec": {"label": "SEC EDGAR", "required": False,
            "role": "Company filings (8-K / 6-K): finds the sponsor's result announcement when you do not paste one"},
    "meterless": {"label": "Meterless World Model (local engine)", "required": False,
                  "role": "Every source, entity and verified fact is also written to the official Meterless World Model "
                          "engine, running on this machine as a local service: event-sourced, audited, idempotent."},
    "zetaris": {"label": "Zetaris (federated data layer)", "required": False,
                "role": "Reads ClinicalTrials.gov, Europe PMC and SEC EDGAR through one Lightning SQL interface and applies "
                        "the literature date cut-off in SQL. Falls back to direct requests if Zetaris is unreachable."},
}


def source_enabled(name: str) -> bool:
    if SOURCES.get(name, {}).get("required"):
        return True
    if SOURCES.get(name, {}).get("coming_soon"):
        return False
    return _flag(f"SOURCE_{name.upper()}", "true")


def int_setting(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def max_literature_docs() -> int:
    return int_setting("MAX_LITERATURE_DOCS", 6, 1, 15)


def max_critic_rounds() -> int:
    return int_setting("MAX_CRITIC_ROUNDS", 2, 1, 3)
