from __future__ import annotations

import re
import unicodedata
from typing import Any


BRASILEIRAO_SERIE_A_ID = 71
COPA_LIBERTADORES_ID = 13
COPA_SUDAMERICANA_ID = 11

COMPETITION_IDS = {
    BRASILEIRAO_SERIE_A_ID: "brasileirao_serie_a",
    COPA_LIBERTADORES_ID: "copa_libertadores",
    COPA_SUDAMERICANA_ID: "copa_sudamericana",
}

COMPETITION_ALIASES = {
    "brasileirao_serie_a": {
        "brasileirao serie a", "campeonato brasileiro serie a", "brazil serie a",
    },
    "copa_libertadores": {
        "conmebol libertadores", "copa conmebol libertadores", "copa libertadores",
        "libertadores", "taca libertadores da america",
    },
    "copa_sudamericana": {
        "conmebol sudamericana", "copa conmebol sudamericana", "copa sudamericana",
        "copa sul americana", "sul americana", "sudamericana",
    },
}

_STAGE_WORDS = {
    "group", "groups", "league", "stage", "fase", "phase", "qualification", "qualifying", "round",
    "rodada", "regular", "quarter", "quarterfinals", "semifinals", "final",
    "oitavas", "quartas", "semifinal", "playoff", "playoffs",
}


def normalize_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.casefold()).split())


def competition_kind(league_id: Any, name: str, country: str = "") -> str | None:
    try:
        stable_id = int(league_id)
    except (TypeError, ValueError):
        stable_id = None
    if stable_id in COMPETITION_IDS:
        return COMPETITION_IDS[stable_id]
    normalized = normalize_name(name)
    if normalized == "serie a" and normalize_name(country) == "brazil":
        return "brasileirao_serie_a"
    for kind, aliases in COMPETITION_ALIASES.items():
        for alias in aliases:
            if normalized == alias:
                return kind
            suffix = normalized.removeprefix(alias).strip()
            if suffix and normalized.startswith(alias + " ") and suffix.split()[0] in _STAGE_WORDS:
                return kind
    return None
