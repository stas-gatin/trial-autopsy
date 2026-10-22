"""Meterless Runtime (Nightshift) as an OpenAI-compatible model provider.

Implements the Meterless Runtime 1.4 provider contract (v4) from the Power prompt at meterless.ai/engines.html:
- provider id `meterless`, default base URL http://127.0.0.1:8088/v1; pasted URLs are normalised to <host>/v1;
- `Authorization: Bearer <runtime token>` on every call (the placeholder rt-local is rejected);
- `meterless-app: trial-autopsy` on every request and `meterless-scope: user` on chat;
- model ids are routing modes (meterless/deep, meterless/fast, ...), read from GET /v1/models;
- GET /v1/models decides the state: 401/403 unauthorized, 200 with meterless ids online, anything else offline;
- Auto Detect tries ports 8088, 8090, 8787 and 11434 on 127.0.0.1, localhost and [::1];
- a connect link http://<host>:<port>/connect#k=<key> gives the base URL and the token (never logged).

The NVIDIA key lives in Nightshift's Providers tab; this app only holds the Nightshift URL and runtime token.
Lanes are set up in Nightshift: the deep lane runs Nemotron 3 Ultra, the fast lane Nemotron 3.5 Super.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8088/v1"
APP_ID = "trial-autopsy"
PLACEHOLDER_TOKENS = {"rt-local", ""}
DETECT_PORTS = (8088, 8090, 8787, 11434)
DETECT_HOSTS = ("127.0.0.1", "localhost", "[::1]")

# Which NVIDIA model each routing mode runs on in our Nightshift lanes. Used to replay recordings made with that
# model and, if Nightshift is down, for the direct NVIDIA fallback. Override with METERLESS_ROUTE_<MODE>.
ROUTE_MODELS = {
    "meterless/deep": "nvidia/nemotron-3-ultra-550b-a55b",
    "meterless/fast": "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "meterless/auto": "nvidia/nemotron-3.5-super-vl-120b-a12b",
}


def normalise_url(url: str | None) -> str:
    """<scheme>://<host>[:port]/v1 from anything a user might paste (with or without scheme or a known path)."""
    url = (url or "").strip()
    if not url:
        return DEFAULT_BASE_URL
    if not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    parsed = urlparse(url)
    path = re.sub(r"/(v1/chat/completions|v1/models|healthz|v1)/*$", "", parsed.path.rstrip("/"))
    path = re.sub(r"/v1$", "", path)
    return f"{parsed.scheme}://{parsed.netloc}{path}/v1"


def parse_connect_link(link: str) -> tuple[str, str] | None:
    """(base URL, token) from http://<host>:<port>/connect#k=<key>, else None."""
    m = re.match(r"^(https?://[^/\s#]+)/connect#k=([^&\s]+)", (link or "").strip())
    if not m:
        return None
    return f"{m.group(1)}/v1", m.group(2)


def base_url() -> str:
    return normalise_url(os.getenv("METERLESS_BASE_URL"))


def token() -> str | None:
    t = (os.getenv("METERLESS_RUNTIME_TOKEN") or "").strip()
    return None if t in PLACEHOLDER_TOKENS or t.startswith("your-") else t


def headers(chat: bool = False) -> dict:
    h = {"meterless-app": APP_ID}
    if chat:
        h["meterless-scope"] = "user"
    return h


def route_model(route: str) -> str | None:
    """The NVIDIA model a routing mode runs on in our lanes."""
    env = os.getenv("METERLESS_ROUTE_" + route.split("/")[-1].replace(".", "_").upper())
    return (env or "").strip() or ROUTE_MODELS.get(route)


def status(url: str | None = None, tok: str | None = None, timeout: float = 5.0) -> dict:
    """online / unauthorized / offline, decided by GET /v1/models (a /healthz answer alone proves nothing)."""
    url = normalise_url(url) if url else base_url()
    tok = tok if tok is not None else token()
    try:
        r = httpx.get(f"{url}/models", headers={"Authorization": f"Bearer {tok or 'none'}", **headers()}, timeout=timeout)
    except httpx.HTTPError as exc:
        return {"state": "offline", "url": url, "message": f"no answer at {url}: {type(exc).__name__}"}
    if r.status_code in (401, 403):
        return {"state": "unauthorized", "url": url, "message": "Nightshift is running but the token was refused: "
                                                                 "paste the API key from Nightshift's Console"}
    if r.status_code == 200:
        try:
            ids = [m.get("id") for m in (r.json().get("data") or []) if isinstance(m, dict)]
        except ValueError:
            ids = []
        routes = [i for i in ids if isinstance(i, str) and i.startswith("meterless/")]
        if routes:
            return {"state": "online", "url": url, "models": routes,
                    "message": f"online: {len(routes)} routing modes ({', '.join(routes[:6])})"}
    return {"state": "offline", "url": url, "message": f"unexpected answer HTTP {r.status_code} from {url}/models"}


def auto_detect(tok: str | None = None) -> dict:
    """The first online or unauthorized runtime on the usual local ports."""
    for port in DETECT_PORTS:
        for host in DETECT_HOSTS:
            st = status(f"http://{host}:{port}/v1", tok, timeout=1.5)
            if st["state"] in ("online", "unauthorized"):
                return st
    return {"state": "offline", "url": None, "message": "no Meterless Runtime found on ports "
                                                        + ", ".join(map(str, DETECT_PORTS))}
