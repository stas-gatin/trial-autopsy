"""One injected generator for every agent (the Meterless "injected generator" seam).

Agents call `generate(role, system, prompt)`. The role decides provider and model
from .env (ROLE_REASONING=anthropic:claude-opus-5-5, ...). Every live response is
recorded to data/llm_cache/ so SAMPLE_MODE can replay it without keys.

Replay keys are a hash of (role, system, prompt): prompts must be deterministic. Recordings are kept per
model (data/llm_cache/<model>/<key>.json), so a Nemotron run never overwrites a Claude recording of the same
call; recordings made before that split live in data/llm_cache/<key>.json and are the fallback on replay.
A replay miss raises PendingModelCall, so a missing recording is visible instead of silently faked.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from app import config

FALLBACK_BETA = "server-side-fallback-2026-07-01"
EFFORT = {"reasoning": "high", "critic": "medium", "extraction": "low", "reporter": "medium"}
# Nemotron 3 reasons step by step only where the role needs it; extraction and writing stay fast.
NVIDIA_THINKING = {"reasoning": True, "critic": True, "extraction": False, "reporter": False}
# NVIDIA's free API allows about 40 requests a minute per key; stay under it across all threads.
STREAM_LIMIT_S = int(__import__("os").getenv("METERLESS_STREAM_LIMIT_S", "600"))   # one Nightshift answer, end to end
STREAM_IDLE_S = int(__import__("os").getenv("METERLESS_STREAM_IDLE_S", "120"))     # longest silence inside a stream
NVIDIA_MIN_INTERVAL = float(__import__("os").getenv("NVIDIA_MIN_INTERVAL", "2.0"))
_nvidia_lock = threading.Lock()
_nvidia_next = [0.0]


def _nvidia_slot():
    """Wait for the next request slot (shared by every thread), so parallel runs never burst past the limit."""
    with _nvidia_lock:
        wait = _nvidia_next[0] - time.time()
        _nvidia_next[0] = max(time.time(), _nvidia_next[0]) + NVIDIA_MIN_INTERVAL
    if wait > 0:
        time.sleep(wait)


# When a large Nemotron model is overloaded on NVIDIA's free API, the call moves to the next model in line.
NVIDIA_FALLBACK = {"nvidia/nemotron-3-ultra-550b-a55b": "nvidia/nemotron-3.5-super-vl-120b-a12b"}
# NVIDIA retired the Nemotron 3 Super endpoint: live calls go to its successor, and recordings made with the retired
# model still replay for the successor's configuration (each recording names the model that actually answered).
RETIRED = {"nvidia/nemotron-3-super-120b-a12b": "nvidia/nemotron-3.5-super-vl-120b-a12b"}
PREDECESSOR = {new: old for old, new in RETIRED.items()}


class PendingModelCall(RuntimeError):
    def __init__(self, key: str, role: str):
        super().__init__(f"no recorded model output for {role} call {key}")
        self.key = key
        self.role = role


class PendingBatch(RuntimeError):
    """Several model calls in one step had no recording."""

    def __init__(self, calls: list[PendingModelCall]):
        super().__init__(f"{len(calls)} model outputs not recorded")
        self.calls = calls


class ModelRefused(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    key: str
    cached: bool
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_estimated: bool = False   # True when the recording has no billed usage (~4 characters per token)


def _estimate(text: str) -> int:
    return max(1, round(len(text) / 4))


def request_key(role: str, system: str, prompt: str) -> str:
    blob = json.dumps({"role": role, "system": system, "prompt": prompt}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:20]


def _slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", model)


def _cache_path(key: str, model: str | None = None):
    return (config.LLM_CACHE / _slug(model) if model else config.LLM_CACHE) / f"{key}.json"


def _record(key: str, role: str, provider: str, model: str, system: str, prompt: str, text: str, recorded_with: str,
            usage: dict | None = None, cache_model: str | None = None):
    path = _cache_path(key, cache_model)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "key": key, "role": role, "provider": provider, "model": model,
        "recorded_with": recorded_with,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "system": system, "prompt": prompt, "response": text, **({"usage": usage} if usage else {}),
    }, indent=2, ensure_ascii=False))


def _model_paths(key: str, model: str | None):
    """Recording locations for `model`: its own folder, then its retired predecessor's. A Meterless routing mode
    (meterless/deep) also looks in the folder of the Nemotron model its Nightshift lane runs."""
    if not model:
        return ()
    from app.llm import meterless_runtime
    models = [model, PREDECESSOR.get(model)]
    if model.startswith("meterless/"):
        # Nightshift picks the lane, so a call made with this route may have been answered on any of our lanes
        lane = meterless_runtime.route_model(model)
        models += [lane, PREDECESSOR.get(lane)]
        for other in meterless_runtime.ROUTE_MODELS.values():
            models += [other, PREDECESSOR.get(other)]
    seen = []
    for m in models:
        if m and m not in seen:
            seen.append(m)
    return tuple(_cache_path(key, m) for m in seen)


def _replay(key: str, model: str | None = None) -> dict | None:
    """The recording made with `model` (or the model it replaced), else the shared recording from before
    per-model storage."""
    for path in _model_paths(key, model) + (_cache_path(key),):
        if path.exists():
            return json.loads(path.read_text())
    return None



def strip_reasoning(text: str) -> str:
    """The answer without the model's reasoning. Nemotron's reasoning arrives as <think>...</think> before the
    answer; through Nightshift the opening tag can be missing, so everything up to the last </think> goes."""
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return re.sub(r"(?s)^\s*<think>.*", "", text).strip() if text.lstrip().startswith("<think>") else text.strip()


def _call_anthropic(model: str, role: str, system: str, prompt: str) -> tuple[str, dict]:
    import anthropic

    client = anthropic.Anthropic(api_key=config.provider_key("anthropic"), timeout=240.0, max_retries=2)
    kwargs = dict(model=model, max_tokens=16000, system=system,
                  messages=[{"role": "user", "content": prompt}],
                  output_config={"effort": EFFORT.get(role, "medium")})
    try:
        response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
    except anthropic.BadRequestError:
        # Model or account without server-side fallback support: plain request.
        response = client.messages.create(**kwargs)
    if response.stop_reason == "refusal":
        category = getattr(getattr(response, "stop_details", None), "category", None)
        raise ModelRefused(f"model declined ({category})")
    usage = getattr(response, "usage", None)
    return ("".join(block.text for block in response.content if block.type == "text"),
            {"input_tokens": getattr(usage, "input_tokens", 0) or 0, "output_tokens": getattr(usage, "output_tokens", 0) or 0})


def _call_openai_compatible(provider: str, model: str, system: str, prompt: str, role: str = "") -> tuple[str, dict]:
    """OpenAI-compatible call (NVIDIA NIM or any other). Returns (text, usage); usage["model"] is the model that
    actually answered, which differs from `model` when an overloaded Nemotron model fell back to the next one."""
    from openai import APIConnectionError, APIStatusError, BadRequestError, OpenAI

    if provider == "meterless":
        return _call_meterless(model, system, prompt, role)
    model = RETIRED.get(model, model)
    thinking = provider == "nvidia" and NVIDIA_THINKING.get(role, False)
    # A call without reasoning answers in seconds to a minute; NVIDIA's API sometimes holds one open for many minutes,
    # so those calls give up after 3 minutes and try again. Reasoning calls can legitimately take up to 10.
    client = OpenAI(api_key=config.provider_key(provider), base_url=config.base_url(provider),
                    timeout=600.0 if thinking else float(__import__("os").getenv("NVIDIA_FAST_TIMEOUT_S", "180")), max_retries=0)
    extra = {"chat_template_kwargs": {"enable_thinking": thinking}} if provider == "nvidia" else None
    current = model
    for attempt in range(8):
        if provider == "nvidia":
            _nvidia_slot()
        try:
            response = client.chat.completions.create(
                model=current, temperature=0.1, max_tokens=16384 if thinking else 8192, extra_body=extra,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            )
            break
        except APIConnectionError:
            # Timeout or dropped connection (a long Ultra reasoning call): retry, on the next model in line if any.
            if attempt == 7:
                raise
            current = NVIDIA_FALLBACK.get(current, current)
            time.sleep(10)
        except APIStatusError as exc:
            if exc.status_code not in (429, 500, 502, 503, 504) or attempt == 7:
                raise
            if exc.status_code == 503 and attempt >= 1 and current in NVIDIA_FALLBACK:
                current = NVIDIA_FALLBACK[current]
            # A rate limit needs a real pause; overload and server errors a short one.
            time.sleep(min(180, 30 * (attempt + 1)) if exc.status_code == 429 else min(60, 5 * (attempt + 1)))
    usage = getattr(response, "usage", None)
    text = strip_reasoning(response.choices[0].message.content or "")
    return (text, {"input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                   "output_tokens": getattr(usage, "completion_tokens", 0) or 0, "model": current})


def _call_meterless(route: str, system: str, prompt: str, role: str) -> tuple[str, dict]:
    """A chat request through Meterless Runtime (Nightshift), which serves it from the Nemotron lane the routing
    mode points to. If Nightshift cannot be reached, the call goes straight to NVIDIA's API (when a key is set),
    and the usage record says so."""
    from openai import APIConnectionError, APIError, APIStatusError, BadRequestError, OpenAI

    from app.llm import meterless_runtime as mr
    client = OpenAI(api_key=mr.token(), base_url=mr.base_url(), timeout=600.0, max_retries=0,
                    default_headers=mr.headers(chat=True))
    lane = mr.route_model(route)
    # Nightshift keeps a cache of answers keyed on the last 4,000 characters of the prompt, and replays a stored
    # answer for a near-identical prompt (similarity >= 0.92) without saying so. Two runs of one case differ mostly
    # in their numbers, so a stale answer could come back. A random reference at the end makes every request's key
    # unique and keeps the similarity below the replay floor. Only the request carries it; the recording does not.
    sent = f"{prompt}\n\n[request reference, ignore: {secrets.token_hex(200)}]"
    thinking = NVIDIA_THINKING.get(role, False)
    extra = {"chat_template_kwargs": {"enable_thinking": thinking}}
    response, aborted = None, 0
    for attempt in range(9):
        if aborted >= 2:
            break   # Nightshift keeps cutting (or stalling) this request: direct NVIDIA below
        _nvidia_slot()   # Nightshift calls NVIDIA with the same key, so the same rate limit applies
        try:
            # Streamed: Nightshift aborts a cloud request whose response headers take over 45 s, and a long Ultra
            # answer that is not streamed only sends its headers when it is complete.
            stream = client.chat.completions.create(
                model=route, temperature=0.1, max_tokens=16384 if thinking else 8192, extra_body=extra,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": sent}],
                stream=True, stream_options={"include_usage": True})
            parts, answered_model, usage_seen = [], None, None
            # Watchdog: Nightshift can hold a stream open with keep-alives only, which never reach this loop, so a
            # separate thread closes a stream that sends no text for STREAM_IDLE_S or runs past STREAM_LIMIT_S.
            began = last = time.time()
            done, cut = threading.Event(), []
            def _watch():
                while not done.wait(5):
                    idle, total = time.time() - last, time.time() - began
                    if idle > STREAM_IDLE_S or total > STREAM_LIMIT_S:
                        cut.append(f"no text for {idle:.0f} s" if idle > STREAM_IDLE_S else f"ran past {total:.0f} s")
                        try:
                            stream.close()
                        except Exception:
                            pass
                        return
            threading.Thread(target=_watch, daemon=True).start()
            try:
                for chunk in stream:
                    answered_model = getattr(chunk, "model", None) or answered_model
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        parts.append(chunk.choices[0].delta.content)
                        last = time.time()
                    usage_seen = getattr(chunk, "usage", None) or usage_seen
            except Exception:
                if not cut:
                    raise
            finally:
                done.set()
            if cut:
                raise TimeoutError(f"Nightshift stream for {route} aborted by the watchdog: {cut[0]}")
            if not strip_reasoning("".join(parts)).strip() and attempt < 8:
                time.sleep(5)   # the stream ended before the answer (an aborted lane): ask again
                continue
            response = ("".join(parts), answered_model, usage_seen)
            break
        except BadRequestError:
            if extra is None or attempt == 8:
                raise
            extra = None    # a runtime that does not pass the reasoning switch through: plain request
        except APIConnectionError:
            if attempt >= 1:
                break       # Nightshift is not running: fall back below
            time.sleep(3)
        except APIStatusError as exc:
            # 499: Nightshift aborted the upstream fetch (a lane waking up, or NVIDIA busy). A 502 that carries
            # NVIDIA's 429 is the API's rate limit passed through Nightshift: wait like for a 429.
            if exc.status_code not in (429, 499, 500, 502, 503, 504) or attempt == 8:
                raise
            aborted += exc.status_code == 499
            rate_limited = exc.status_code == 429 or "429" in str(exc)
            time.sleep(min(180, 30 * (attempt + 1)) if rate_limited else min(90, 10 * (attempt + 1)))
        except (APIError, TimeoutError) as exc:
            if attempt == 8:
                raise
            aborted += isinstance(exc, TimeoutError) or "aborted" in str(exc).lower()
            time.sleep(min(90, 10 * (attempt + 1)))   # the stream broke off midway, or hung
    if response is None:
        if lane and config.provider_key("nvidia"):
            # Nightshift caps a cloud lane's time to first token (45 s, or less once it has learned the model's
            # usual latency), so a long prompt can be cut every time. That one call then goes to NVIDIA directly,
            # on the model the route's lane runs, and the record says so.
            target = "nvidia/nemotron-3-ultra-550b-a55b" if aborted >= 2 else lane
            text, usage = _call_openai_compatible("nvidia", target, system, prompt, role)
            usage["provider"] = "nvidia"
            usage["note"] = ("Nightshift cut or stalled the request twice: direct NVIDIA API"
                             if aborted >= 2 else "Meterless Runtime unreachable: direct NVIDIA API")
            return text, usage
        raise RuntimeError(f"Meterless Runtime at {mr.base_url()} is not reachable and no NVIDIA key is set")
    raw, answered, usage = response
    answered = answered or route
    text = strip_reasoning(raw)
    if not text.strip():
        raise RuntimeError(f"Meterless Runtime returned an empty answer for {route}")
    # Nightshift's stream may carry no usage: then the tokens are estimated, as for recorded runs
    return (text, {"input_tokens": (getattr(usage, "prompt_tokens", 0) or 0) if usage else _estimate(system + prompt),
                   "output_tokens": (getattr(usage, "completion_tokens", 0) or 0) if usage else _estimate(raw),
                   "model": answered, "route": route, "lane": lane})


def generate(role: str, system: str, prompt: str, tag: str = "", agent: str | None = None) -> LLMResponse:
    # The replay key uses the role GROUP, so per-agent model choices never invalidate recordings.
    key = request_key(role, system, prompt)
    spec = config.agent_spec(agent, role)
    started = time.time()

    live = not config.sample_mode() and spec.provider != "replay" and config.provider_key(spec.provider)
    # LLM_REPLAY_FIRST: a live run that reuses the recording of an identical call (same model, same prompt), so a
    # rerun after a change to one agent only calls the model for the calls that changed.
    if live and config._flag("LLM_REPLAY_FIRST") and any(p.exists() for p in _model_paths(key, spec.model)):
        live = False
    if not live:
        hit = _replay(key, spec.model)
        if hit is None:
            raise PendingModelCall(key, role)
        usage = hit.get("usage")
        return LLMResponse(hit["response"], hit["provider"], hit["model"], key, True, time.time() - started,
                           input_tokens=usage["input_tokens"] if usage else _estimate(system + prompt),
                           output_tokens=usage["output_tokens"] if usage else _estimate(hit["response"]),
                           tokens_estimated=not usage)

    if spec.provider == "anthropic":
        text, usage = _call_anthropic(spec.model, role, system, prompt)
    else:
        text, usage = _call_openai_compatible(spec.provider, spec.model, system, prompt, role)
    answered_by = usage.pop("model", None) or spec.model
    provider = usage.pop("provider", None) or spec.provider
    route, lane = usage.pop("route", None), usage.pop("lane", None)
    note = usage.pop("note", None)
    # Stored under the configured model, so the same configuration replays it; the record names the model that
    # answered. Through Meterless the recording goes to the folder of the model the lane actually ran (e.g.
    # nvidia/nemotron-3-ultra-550b-a55b, or openrouter/nvidia/nemotron-3.5-lightning), never to another model's
    # folder, so a Lightning answer can never replay as an Ultra one.
    cache_model = spec.model
    if spec.provider == "meterless":
        cache_model = answered_by if answered_by and not answered_by.startswith("meterless/") else (lane or spec.model)
    recorded_with = "live API" + (f" via Meterless Runtime ({route})" if provider == "meterless" else "") + \
                    (f"; {note}" if note else "")
    _record(key, role, provider, answered_by, system, prompt, text, recorded_with=recorded_with, usage=usage,
            cache_model=cache_model)
    return LLMResponse(text, provider, answered_by, key, False, time.time() - started,
                       input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"])


_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def parse_json(text: str):
    """Parse the first JSON object/array in a model reply (fenced or bare)."""
    match = _FENCE.search(text)
    candidate = match.group(1) if match else text
    candidate = candidate.strip()
    if not match:
        start = min((i for i in (candidate.find("{"), candidate.find("[")) if i >= 0), default=-1)
        if start < 0:
            raise ValueError("no JSON in model reply")
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        candidate = candidate[start:end + 1]
    return json.loads(candidate)


REPAIR_SYSTEM = """You fix malformed JSON. Return the same content as one valid JSON value, changing nothing
but the syntax (missing commas, quotes, brackets, trailing text). JSON only."""


def _as_object(data, list_key: str | None):
    """Models sometimes return a bare list where an object was asked for: wrap it under the expected key, or take
    the single object a one-element list holds."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        if list_key:
            return {list_key: data}
        if len(data) == 1 and isinstance(data[0], dict):
            return data[0]
    raise ValueError(f"expected a JSON object, got {type(data).__name__}")


def generate_json(role: str, system: str, prompt: str, tag: str = "", agent: str | None = None,
                  list_key: str | None = None, retry: bool = True):
    response = generate(role, system, prompt, tag, agent)
    try:
        return _as_object(parse_json(response.text), list_key), response
    except ValueError:
        pass
    try:
        # One repair pass by the same model: the content stays the model's own, only the syntax is fixed.
        fixed = generate(role, REPAIR_SYSTEM, response.text, f"{tag}:json-repair", agent)
        return _as_object(parse_json(fixed.text), list_key), response
    except ValueError:
        if not retry:
            raise
    # Nothing to repair (e.g. a reply cut off while the model was still reasoning aloud): ask once more. The retry
    # has its own prompt, hence its own recording, and replays exactly like the live run. Extraction passes
    # retry=False: it skips a document it cannot parse and logs it.
    retry = generate(role, system, prompt + "\n\nReturn only the JSON object, with no text before it.",
                     f"{tag}:retry", agent)
    try:
        return _as_object(parse_json(retry.text), list_key), retry
    except ValueError:
        fixed = generate(role, REPAIR_SYSTEM, retry.text, f"{tag}:retry:json-repair", agent)
        return _as_object(parse_json(fixed.text), list_key), retry
