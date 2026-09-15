from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def is_transient_apimart_error(error: Exception) -> bool:
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    message = str(error).casefold()
    permanent = (
        "credential", "api key", "unauthorized", "forbidden", "http 400", "http 401",
        "http 403", "http 404", "http 422", "invalid", "unsupported", "exceeds 20mb",
        "content safety", "内容安全", "asset is missing",
    )
    if any(marker in message for marker in permanent):
        return False
    return bool(
        re.search(r"http (?:408|409|425|429|5\d\d)\b", message)
        or any(marker in message for marker in ("timeout", "timed out", "rate limit", "temporar", "unavailable", "connection", "fetch failed", "task failed", "cancelled", "downloaded video is too small"))
    )


def retry_forever(
    operation: Callable[[], T], *, state_path: Path, operation_name: str,
    base_delay: float = 20.0, max_delay: float = 1800.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    state: dict[str, Any] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    if state.get("status") == "retry_wait" and state.get("next_retry_at"):
        try:
            resume_at = datetime.fromisoformat(str(state["next_retry_at"]))
            remaining = max(0.0, resume_at.timestamp() - time.time())
        except (TypeError, ValueError):
            remaining = 0.0
        if remaining:
            print(
                f"[apimart] operation={operation_name} resuming_wait next_retry_at={state['next_retry_at']}",
                flush=True,
            )
            sleeper(remaining)
    attempt = int(state.get("attempt", 0))
    while True:
        attempt += 1
        started_at = datetime.now(timezone.utc)
        try:
            result = operation()
        except Exception as error:
            if not is_transient_apimart_error(error):
                _write_state(state_path, {
                    "operation": operation_name, "attempt": attempt, "status": "permanent_failure",
                    "failed_at": started_at.isoformat(), "error": _sanitize(error),
                })
                raise
            delay = min(max_delay, base_delay * (2 ** min(attempt - 1, 8)))
            delay *= random.uniform(0.8, 1.2)
            next_retry = datetime.fromtimestamp(time.time() + delay, timezone.utc).isoformat()
            _write_state(state_path, {
                "operation": operation_name, "attempt": attempt, "status": "retry_wait",
                "failed_at": started_at.isoformat(), "error": _sanitize(error),
                "delay_seconds": round(delay, 3), "next_retry_at": next_retry,
            })
            print(f"[apimart] operation={operation_name} attempt={attempt} failed={_sanitize(error)} next_retry_at={next_retry}", flush=True)
            sleeper(delay)
            continue
        _write_state(state_path, {
            "operation": operation_name, "attempt": attempt, "status": "succeeded",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"[apimart] operation={operation_name} attempt={attempt} succeeded", flush=True)
        return result


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prior: dict[str, Any] = {}
    if path.is_file():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior = {}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({**prior, **payload}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sanitize(error: Exception) -> str:
    message = re.sub(r"(?:sk-|Bearer\s+)[A-Za-z0-9._-]+", "[credential redacted]", str(error))
    return f"{type(error).__name__}: {message[:500]}"
