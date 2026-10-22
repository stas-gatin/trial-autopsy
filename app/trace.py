"""JSONL trace logging: one file per run in logs/, mirrored to stdout."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from datetime import datetime, timezone

from app.config import LOGS

_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]+|(nvapi-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]+")


def _mask(text: str) -> str:
    return _SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "...redacted", text)


class Tracer:
    def __init__(self, run_id: str, echo: bool = True):
        self.run_id = run_id
        self.echo = echo
        self.started = time.time()
        self.events: list[dict] = []
        self._lock = threading.Lock()
        LOGS.mkdir(parents=True, exist_ok=True)
        self.trace_id = f"trace-{run_id}"
        self.path = LOGS / f"trace-{run_id}.jsonl"
        self.path.write_text("")

    @property
    def log_file(self) -> str:
        return f"logs/{self.path.name}"

    def log(self, agent: str, action: str, *, input_summary: str = "", output_summary: str = "",
            target_agent: str | None = None, confidence: float | None = None, retry_count: int = 0,
            status: str = "success", **extra) -> dict:
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "agent_name": agent,
            "action": action,
            "input_summary": _mask(input_summary)[:500],
            "output_summary": _mask(output_summary)[:800],
            "target_agent": target_agent,
            "confidence": confidence,
            "retry_count": retry_count,
            "status": status,
            "elapsed_s": round(time.time() - self.started, 2),
            **extra,
        }
        with self._lock:
            self.events.append(event)
            with self.path.open("a") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if self.echo:
            arrow = f" -> {target_agent}" if target_agent else ""
            level = "ERROR" if status == "error" else ("WARN" if status in ("warning", "retry", "needs_revision") else "INFO")
            print(f"[{level}] run_id={self.run_id} {agent}{arrow}: {action} | {event['output_summary'][:160]}",
                  file=sys.stderr)
        return event
