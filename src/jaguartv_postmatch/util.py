from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def executable_path(name: str) -> str | None:
    discovered = shutil.which(name)
    if discovered:
        return discovered
    local = Path.home() / ".local" / "bin" / name
    return str(local) if local.is_file() else None


def tool_environment() -> dict[str, str]:
    environment = os.environ.copy()
    local_bin = str(Path.home() / ".local" / "bin")
    environment["PATH"] = f"{local_bin}:{environment.get('PATH', '')}"
    return environment


CURRENT_TASK_MODEL_NAMES = {"", "auto", "current", "current-task", "active-runtime", "inherit"}


def codex_model_args(model: str | None, provider: str | None = None) -> list[str]:
    selected = (model or "").strip()
    if selected.casefold() in CURRENT_TASK_MODEL_NAMES:
        return []
    args = []
    if provider:
        args.extend(["-c", f'model_provider="{provider}"'])
    return [*args, "-m", selected]


MONTHS_PT_BR = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).split())


TEAM_ALIASES = {
    "brighton hove albion": "brighton",
    "brighton and hove albion": "brighton",
    "ipswich town": "ipswich",
    "chapecoense sc": "chapecoense",
    "gremio fbpa": "gremio",
    "malaga cf": "malaga",
    # 09-01 international/lower-league name bridging (copa.jarg.top <-> ESPN displayName)
    "wolfsberger ac": "wolfsberger",
    "norwich": "norwich city",
    "fbc melgar": "melgar",
    # 09-02 copa.jarg.top (short) <-> ESPN displayName (full) bridging
    "vasco da gama": "vasco",
    "velez sarsfield": "velez",
    "boca juniors": "boca",
}


def canonical_team(value: str) -> str:
    normalized = normalize_name(value)
    return TEAM_ALIASES.get(normalized, normalized)


def parse_pt_br_manifest_date(value: str) -> str:
    match = re.fullmatch(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", value.strip().upper())
    if not match or match.group(2) not in MONTHS_PT_BR:
        raise ValueError(f"unsupported Task 1 date: {value!r}")
    day, month_name, year = match.groups()
    return f"{int(year):04d}-{MONTHS_PT_BR[month_name]:02d}-{int(day):02d}"


def parse_manifest_time(value: str) -> str:
    match = re.fullmatch(r"(\d{1,2})H(\d{2})", value.strip().upper())
    if not match:
        raise ValueError(f"unsupported Task 1 kickoff: {value!r}")
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_filename_part(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]", "-", value.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned or "未命名"


def single_poster_filename(
    home_zh: str,
    home_score: int,
    away_score: int,
    away_zh: str,
    yymmdd: str,
) -> str:
    return (
        f"{sanitize_filename_part(home_zh)}-{home_score}：{away_score}-"
        f"{sanitize_filename_part(away_zh)}_{yymmdd}_海报.png"
    )
