"""Settings page backend: data sources, model providers and keys, run options.

Values live in environment variables and are persisted to the local .env file (git-ignored).
API keys are write-only from the UI: reads return a masked hint, never the key itself.
"""
from __future__ import annotations

import os
import re
import time

from app import config

ENV_PATH = config.ROOT / ".env"

PROVIDERS = {
    "meterless": {"label": "Meterless Runtime (Nightshift)", "key_env": "METERLESS_RUNTIME_TOKEN",
                  "base_env": "METERLESS_BASE_URL", "key_label": "Runtime token",
                  "key_placeholder": "Nightshift → Console → API key (or paste a connect link in Base URL)",
                  "base_placeholder": "http://127.0.0.1:8088/v1", "detect": True,
                  "models": ["meterless/deep", "meterless/fast", "meterless/auto"],
                  "docs": "https://meterless.ai"},
    "anthropic": {"label": "Anthropic (Claude)", "key_env": "ANTHROPIC_API_KEY", "base_env": None,
                  "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"],
                  "docs": "https://console.anthropic.com"},
    "nvidia": {"label": "NVIDIA NIM (Nemotron)", "key_env": "NVIDIA_API_KEY", "base_env": "NVIDIA_BASE_URL",
               "models": ["nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-super-vl-120b-a12b",
                          "nvidia/nemotron-3.5-lightning-30b-a3b"], "docs": "https://build.nvidia.com"},
    "openai": {"label": "Any OpenAI-compatible API", "key_env": "OPENAI_API_KEY", "base_env": "OPENAI_BASE_URL",
               "models": [], "docs": ""},
}

ROLES = {
    "reasoning": "Mechanism analyst and investigator: picks the causal pathway, weighs every explanation",
    "extraction": "Reads documents and copies out numbers with verbatim quotes (many calls)",
    "critic": "Reviews inputs and claims, can send work back",
    "reporter": "Writes the analyst-facing report",
}

# Model catalogue shown next to each agent: capability tier and price per million tokens (input/output).
MODEL_INFO = {
    "claude-fable-5-1": {"tier": "Frontier", "price": "$10 / $50"},
    "claude-opus-5-5": {"tier": "Most capable", "price": "$4 / $20"},
    "claude-sonnet-5-5": {"tier": "Balanced", "price": "$2 / $10"},
    "claude-haiku-4-5": {"tier": "Fast & cheap", "price": "$1 / $5"},
    "meterless/deep": {"tier": "Nightshift deep route: Nightshift picks the Nemotron lane", "price": "via Meterless Runtime"},
    "meterless/fast": {"tier": "Nightshift fast route: Nightshift picks the Nemotron lane", "price": "via Meterless Runtime"},
    "meterless/auto": {"tier": "Nightshift chooses the lane", "price": "via Meterless Runtime"},
    "nvidia/nemotron-3-ultra-550b-a55b": {"tier": "Most capable open model", "price": "NVIDIA API"},
    "nvidia/nemotron-3.5-super-vl-120b-a12b": {"tier": "Strong, fast open model", "price": "NVIDIA API"},
    "nvidia/nemotron-3-super-120b-a12b": {"tier": "Retired by NVIDIA; recorded runs only", "price": "NVIDIA API"},
    "nvidia/nemotron-3.5-lightning-30b-a3b": {"tier": "Fast open model", "price": "NVIDIA API"},
}
TIER_LABEL = {"top": "Needs the most capable model", "strong": "Needs a strong, independent model",
              "mid": "A balanced model is enough", "fast": "A fast, cheap model is enough"}

RUN_OPTIONS = {
    "SAMPLE_MODE": {"type": "bool", "label": "Offline sample mode",
                    "help": "No network and no keys: replays recorded data and model outputs (judge mode)."},
    "MAX_LITERATURE_DOCS": {"type": "int", "min": 1, "max": 15, "default": 6, "label": "Articles per case",
                            "help": "How many papers the case builder reads. More = slower and costlier, sometimes better."},
    "MAX_CRITIC_ROUNDS": {"type": "int", "min": 1, "max": 3, "default": 2, "label": "Critic passes",
                          "help": "How many times the critic may review (and send back) the analysis."},
    "CONTACT_EMAIL": {"type": "str", "label": "Contact e-mail for data-source requests",
                      "help": "Sent in the User-Agent of every request to public databases. SEC EDGAR requires it "
                              "(fair-access policy) and may block requests without a real address. No registration, no e-mails."},
}

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-.:]{8,300}$")


def _mask(value: str | None) -> str | None:
    if not value or value.startswith("your-"):
        return None
    return f"•••• {value[-4:]}" if len(value) > 12 else "••••"


def read() -> dict:
    roles = {}
    for name, description in ROLES.items():
        spec = config.role(name)
        roles[name] = {"provider": spec.provider, "model": spec.model, "description": description,
                       "live": bool(config.provider_key(spec.provider)) and not config.sample_mode()}
    return {
        "sources": [{"id": sid, **meta, "enabled": config.source_enabled(sid)} for sid, meta in config.SOURCES.items()],
        "providers": [{"id": pid, "label": p["label"], "models": p["models"], "docs": p["docs"],
                       "key_hint": _mask(os.getenv(p["key_env"])), "configured": bool(config.provider_key(pid)),
                       "base_url": config.base_url(pid) if p["base_env"] else None,
                       "needs_base_url": p["base_env"] is not None, "key_label": p.get("key_label", "API key"),
                       "key_placeholder": p.get("key_placeholder"), "base_placeholder": p.get("base_placeholder"),
                       "detect": bool(p.get("detect"))}
                      for pid, p in PROVIDERS.items()],
        "roles": roles,
        "agents": _agents(),
        "run": {key: {**meta, "value": _run_value(key, meta)} for key, meta in RUN_OPTIONS.items()},
        "zetaris": _zetaris(),
    }


def _zetaris() -> dict:
    from app.tools import zetaris
    s = zetaris.settings()
    return {"url": s["url"], "org": s["org"], "key_hint": _mask(s["key"]), "configured": zetaris.configured(),
            "active": zetaris.enabled()}


def _agents() -> list[dict]:
    out = []
    for a in config.AGENTS:
        item = {"name": a["name"], "kind": a["kind"], "does": a["does"]}
        if a["kind"] == "ai":
            spec = config.agent_spec(a["name"], a["group"])
            rec_provider, _, rec_model = a["recommended"].partition(":")
            alt_provider, _, alt_model = a["alternative"].partition(":")
            item.update({
                "group": a["group"], "tier": a["tier"], "tier_label": TIER_LABEL[a["tier"]], "why": a["why"],
                "provider": spec.provider, "model": spec.model, "model_info": MODEL_INFO.get(spec.model),
                "own_setting": bool(os.getenv(f"AGENT_{a['name'].upper()}")),
                "live": bool(config.provider_key(spec.provider)) and not config.sample_mode(),
                "recommended": {"provider": rec_provider, "model": rec_model, **MODEL_INFO.get(rec_model, {})},
                "alternative": {"provider": alt_provider, "model": alt_model, **MODEL_INFO.get(alt_model, {})},
                "is_recommended": spec.provider == rec_provider and spec.model == rec_model,
            })
        out.append(item)
    return out


def _run_value(key: str, meta: dict):
    raw = os.getenv(key)
    if meta["type"] == "bool":
        return config.sample_mode() if key == "SAMPLE_MODE" else (raw or "").lower() in ("1", "true", "yes")
    if meta["type"] == "int":
        return config.int_setting(key, meta["default"], meta["min"], meta["max"])
    return raw or ""


def update(payload: dict) -> dict:
    """Apply a partial update from the Settings page. Returns the new settings (keys masked)."""
    changes: dict[str, str | None] = {}

    for sid, enabled in (payload.get("sources") or {}).items():
        meta = config.SOURCES.get(sid)
        if meta and not meta.get("required") and not meta.get("coming_soon"):
            changes[f"SOURCE_{sid.upper()}"] = "true" if enabled else "false"

    z = payload.get("zetaris") or {}
    if "url" in z:
        url = (z.get("url") or "").strip().rstrip("/")
        if url and not re.match(r"^https?://[^\s]+/api/v1\.0$", url):
            raise ValueError("Zetaris API URL must end in /api/v1.0, e.g. http://localhost:8888/api/v1.0")
        changes["ZETARIS_REST_URL"] = url or None
    if "org" in z:
        org = str(z.get("org") or "").strip()
        if org and not org.isdigit():
            raise ValueError("Zetaris organisation ID is the numeric ID under Management -> Organisation")
        changes["ZETARIS_ORG_ID"] = org or None
    if z.get("clear_key"):
        changes["ZETARIS_API_KEY"] = None
    elif (z.get("key") or "").strip():
        key = z["key"].strip()
        if not re.match(r"^[A-Za-z0-9_\-.:=+/]{8,500}$", key):
            raise ValueError("Zetaris: the key contains characters a key cannot have")
        changes["ZETARIS_API_KEY"] = key

    for pid, data in (payload.get("providers") or {}).items():
        p = PROVIDERS.get(pid)
        if not p:
            continue
        if pid == "meterless":
            _meterless_update(data, changes)
            continue
        key = (data.get("key") or "").strip()
        if data.get("clear_key"):
            changes[p["key_env"]] = None
        elif key:
            if not _KEY_RE.match(key):
                raise ValueError(f"{p['label']}: the key contains characters a key cannot have")
            changes[p["key_env"]] = key
        if p["base_env"] and "base_url" in data:
            url = (data.get("base_url") or "").strip()
            if url and not re.match(r"^https?://[^\s]+$", url):
                raise ValueError(f"{p['label']}: base URL must start with http:// or https://")
            changes[p["base_env"]] = url or None

    for role, data in (payload.get("roles") or {}).items():
        if role not in ROLES:
            continue
        provider = (data.get("provider") or "").strip()
        model = (data.get("model") or "").strip()
        if provider not in PROVIDERS and provider != "replay":
            raise ValueError(f"unknown provider for {role}: {provider}")
        if not re.match(r"^[A-Za-z0-9_\-./:]{1,120}$", model):
            raise ValueError(f"invalid model name for {role}")
        changes[f"ROLE_{role.upper()}"] = f"{provider}:{model}"

    ai_agents = {a["name"]: a for a in config.AGENTS if a["kind"] == "ai"}
    if payload.get("apply_recommended"):
        for name, a in ai_agents.items():
            changes[f"AGENT_{name.upper()}"] = a["recommended"]
    for name, data in (payload.get("agents") or {}).items():
        if name not in ai_agents:
            continue
        if data.get("reset"):
            changes[f"AGENT_{name.upper()}"] = None
            continue
        provider = (data.get("provider") or "").strip()
        model = (data.get("model") or "").strip()
        if provider not in PROVIDERS and provider != "replay":
            raise ValueError(f"unknown provider for {name}: {provider}")
        if not re.match(r"^[A-Za-z0-9_\-./:]{1,120}$", model):
            raise ValueError(f"invalid model name for {name}")
        changes[f"AGENT_{name.upper()}"] = f"{provider}:{model}"

    for key, value in (payload.get("run") or {}).items():
        meta = RUN_OPTIONS.get(key)
        if not meta:
            continue
        if meta["type"] == "bool":
            changes[key] = "true" if value else "false"
        elif meta["type"] == "int":
            changes[key] = str(max(meta["min"], min(meta["max"], int(value))))
        else:
            value = str(value).strip()
            if key == "CONTACT_EMAIL" and value and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
                raise ValueError("contact e-mail is not a valid address")
            changes[key] = value or None

    _apply(changes)
    return read()


def _meterless_update(data: dict, changes: dict):
    """Meterless Runtime: base URL (normalised, or taken from a pasted connect link) and runtime token."""
    from app.llm import meterless_runtime as mr
    url = (data.get("base_url") or "").strip()
    link = mr.parse_connect_link(url)
    if link:
        changes["METERLESS_BASE_URL"], changes["METERLESS_RUNTIME_TOKEN"] = link
    elif "base_url" in data:
        changes["METERLESS_BASE_URL"] = mr.normalise_url(url) if url else None
    if data.get("clear_key"):
        changes["METERLESS_RUNTIME_TOKEN"] = None
    elif (data.get("key") or "").strip() and not link:
        tok = data["key"].strip()
        if tok in mr.PLACEHOLDER_TOKENS:
            raise ValueError("Meterless Runtime: rt-local is a placeholder; paste the API key from Nightshift's Console")
        if not re.match(r"^[A-Za-z0-9_\-.:=+/]{6,500}$", tok):
            raise ValueError("Meterless Runtime: the token contains characters a token cannot have")
        changes["METERLESS_RUNTIME_TOKEN"] = tok


def meterless_detect() -> dict:
    """Auto Detect: find a running Nightshift on the usual local ports and save its address."""
    from app.llm import meterless_runtime as mr
    found = mr.auto_detect()
    if found.get("url"):
        _apply({"METERLESS_BASE_URL": found["url"]})
    return {"ok": found["state"] == "online", **found}


def _apply(changes: dict[str, str | None]):
    """Set in this process and persist to .env, keeping other lines and comments."""
    for key, value in changes.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    done, out = set(), []
    for line in lines:
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in changes:
            key = m.group(1)
            done.add(key)
            if changes[key] is not None:
                out.append(f"{key}={changes[key]}")
            continue
        out.append(line)
    for key, value in changes.items():
        if key not in done and value is not None:
            out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


def test_provider(pid: str, model: str | None = None) -> dict:
    """Smallest possible live call to check a key. Never returns the key."""
    p = PROVIDERS.get(pid)
    if not p:
        return {"ok": False, "message": "unknown provider"}
    if pid == "meterless":
        return _test_meterless(model)
    key = config.provider_key(pid)
    if not key:
        return {"ok": False, "message": "no key saved"}
    model = model or (p["models"][0] if p["models"] else None)
    if not model:
        return {"ok": False, "message": "choose a model first"}
    started = time.time()
    try:
        if pid == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=0)
            response = client.messages.create(model=model, max_tokens=16,
                                              messages=[{"role": "user", "content": "Reply with the word OK."}])
            text = "".join(b.text for b in response.content if b.type == "text")
        else:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=config.base_url(pid), timeout=30.0, max_retries=0)
            response = client.chat.completions.create(model=model, max_tokens=16,
                                                      messages=[{"role": "user", "content": "Reply with the word OK."}])
            text = response.choices[0].message.content or ""
        return {"ok": True, "message": f"{model} answered in {time.time() - started:.1f}s", "reply": text[:40]}
    except Exception as exc:  # report the provider's error type and message, without the key
        message = re.sub(r"(sk-|nvapi-)[A-Za-z0-9_\-]+", "[key]", str(exc))[:300]
        return {"ok": False, "message": f"{type(exc).__name__}: {message}"}


def _test_meterless(model: str | None) -> dict:
    """The contract's checks: GET /v1/models decides online / unauthorized / offline, then one plain text request."""
    from app.llm import meterless_runtime as mr
    st = mr.status()
    if st["state"] != "online":
        return {"ok": False, "state": st["state"], "message": st["message"]}
    route = model if (model or "").startswith("meterless/") else "meterless/auto"
    started = time.time()
    try:
        from openai import OpenAI
        client = OpenAI(api_key=mr.token(), base_url=mr.base_url(), timeout=180.0, max_retries=0,
                        default_headers=mr.headers(chat=True))
        from openai import APIStatusError
        for attempt in range(3):   # 499: Nightshift aborted the fetch while a lane was waking up; try again
            try:
                response = client.chat.completions.create(model=route, max_tokens=600,   # room for the reasoning
                                                          messages=[{"role": "user", "content": "Reply with the word OK."}])
                break
            except APIStatusError as exc:
                if exc.status_code != 499 or attempt == 2:
                    raise
                time.sleep(2)
        from app.llm.generator import strip_reasoning
        text = strip_reasoning(response.choices[0].message.content or "")
        answered = getattr(response, "model", None) or route
        return {"ok": isinstance(text, str) and bool(text), "state": "online",
                "message": f"{st['message']}; {route} answered with {answered} in {time.time() - started:.1f}s",
                "reply": text[:40]}
    except Exception as exc:
        return {"ok": False, "state": "online", "message": f"{type(exc).__name__}: {str(exc)[:240]}"}


def test_source(sid: str) -> dict:
    from app.tools import http
    probes = {
        "ctgov": ("GET", "https://clinicaltrials.gov/api/v2/version", None),
        "opentargets": ("POST", "https://api.platform.opentargets.org/api/v4/graphql",
                        {"query": "{meta{dataVersion{year month}}}"}),
        "europepmc": ("GET", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=pelacarsen&format=json&pageSize=1", None),
        "genetics_literature": ("GET", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=PCSK9&format=json&pageSize=1", None),
        "sec": ("GET", "https://efts.sec.gov/LATEST/search-index?q=%22pelacarsen%22&forms=8-K", None),
    }
    if sid == "meterless":
        from app.engines import meterless
        started = time.time()
        try:
            h = meterless.health()
            return {"ok": True, "message": f"running: {h.get('engine')} answered in {(time.time() - started) * 1000:.0f} ms"}
        except ConnectionError as exc:
            return {"ok": False, "message": f"{exc}. Start it with: npm --prefix services/meterless start"}
    if sid == "zetaris":
        if config.sample_mode():
            return {"ok": True, "message": "offline sample mode: recorded data is used, Zetaris is not called"}
        from app.tools import zetaris
        started = time.time()
        result = zetaris.status()
        if result["ok"]:
            result["message"] += f" in {(time.time() - started) * 1000:.0f} ms"
        return result
    if sid not in probes:
        return {"ok": False, "message": "not available yet" if config.SOURCES.get(sid, {}).get("coming_soon") else "unknown source"}
    if config.sample_mode():
        return {"ok": True, "message": "offline sample mode: recorded data is used, no request made"}
    method, url, body = probes[sid]
    started = time.time()
    try:
        import httpx
        with httpx.Client(timeout=15.0, headers={"User-Agent": f"trial-autopsy/0.1 ({config.contact_email()})"}) as c:
            r = c.request(method, url, json=body)
        if r.status_code >= 400:
            return {"ok": False, "message": f"HTTP {r.status_code}"}
        return {"ok": True, "message": f"reachable in {(time.time() - started) * 1000:.0f} ms"}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {str(exc)[:200]}"}
