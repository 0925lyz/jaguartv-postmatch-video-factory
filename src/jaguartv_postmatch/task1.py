from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .util import canonical_team, parse_manifest_time, parse_pt_br_manifest_date, stable_json_hash


# Canonical upstream Task 1 (pre-match) factory runs root. Task 3 reads these runs
# first and only falls back to the legacy image2_database outputs tree when they
# contain no usable fixtures for the target date.
PREMATCH_FACTORY_ROOT = Path("/Users/jaguar/WorkBuddy/赛前/jaguartv-prematch-video-factory")


@dataclass(frozen=True)
class Task1Fixture:
    task1_fixture_id: str
    source_fixture_id: str | None
    composite_key: str
    competition: str
    home_team: str
    away_team: str
    match_date: str
    kickoff_brt: str
    channels: list[str]
    home_crest: str
    away_crest: str
    source_manifest: str
    pre_match_poster: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _composite_key(match_date: str, competition: str, home: str, away: str) -> str:
    return stable_json_hash(
        {
            "competition": competition,
            "home": canonical_team(home),
            "away": canonical_team(away),
            "match_date": match_date,
        }
    )


def load_task1_fixtures(
    image2_database: Path,
    target_date: date,
    prematch_root: Path = PREMATCH_FACTORY_ROOT,
) -> list[Task1Fixture]:
    """Load Task 1 fixtures.

    Priority order, as agreed with the operator:
    1. The pre-match factory runs at ``<prematch_root>/runs/<YYYYMMDD>_batch*/``
       (phase1 / phase3 / phase4 manifests).
    2. The legacy ``image2_database/outputs/<YYMMDD>_prematch_image2`` tree.
    """
    fixtures = _load_from_prematch_runs(prematch_root, image2_database, target_date)
    if fixtures:
        return fixtures
    return _load_from_legacy_image2_outputs(image2_database, target_date)


def _prematch_run_dirs(prematch_root: Path, target_date: date) -> list[Path]:
    runs_root = prematch_root / "runs"
    if not runs_root.is_dir():
        return []
    prefix = target_date.strftime("%Y%m%d")
    return sorted(
        (path for path in runs_root.iterdir() if path.is_dir() and path.name.startswith(prefix)),
        key=lambda path: path.name,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _channels(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = [str(item).strip() for item in value]
    elif isinstance(value, str):
        raw = [part.strip() for part in re.split(r"[•,]", value)]
    else:
        raw = []
    # Task 3 channel-icon lookups are keyed by uppercase names (e.g. "PREMIERE"),
    # so normalise the case coming from the pre-match fixtures ("Premiere", "SPORTV").
    return [item.upper() for item in raw if item]


def _normalize_kickoff(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        hour, minute = raw.split(":")
        return f"{int(hour):02d}:{minute}"
    try:
        return parse_manifest_time(raw)
    except ValueError:
        return ""


def _resolve_crest(image2_database: Path, team_name: str) -> str:
    target = canonical_team(team_name)
    crest_root = image2_database / "assets" / "crests"
    if not crest_root.is_dir():
        return ""
    matches: list[Path] = []
    for crest_path in crest_root.rglob("*.png"):
        if canonical_team(crest_path.stem) == target:
            matches.append(crest_path)
    if not matches:
        return ""
    # Newest collection date wins (YYMMDD directory names sort chronologically).
    matches.sort(key=lambda path: path.parent.name, reverse=True)
    return str(matches[0].resolve())


def _prematch_posters(run_dir: Path) -> dict[str, str]:
    """Map Task 1 fixture id -> absolute poster path for a single pre-match run.

    Poster paths in the manifest may be absolute (newer runs) or relative to the
    pre-match factory root (older runs emit "runs/<date>/phase3/posters/...").
    Resolve relative paths against the factory root so ``Path(poster).is_file()``
    succeeds regardless of the current working directory.
    """
    posters: dict[str, str] = {}
    factory_root = run_dir.parent

    def _resolve(value: object) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        path = Path(raw)
        if not path.is_absolute():
            path = (factory_root / path).resolve()
        return str(path)

    phase3 = _read_json(run_dir / "phase3" / "poster-manifest.json")
    for item in (phase3 or {}).get("items") or []:
        if not isinstance(item, dict) or item.get("kind") != "single":
            continue
        task_id = str(item.get("task_id") or "").strip()
        poster = _resolve(item.get("poster"))
        if task_id and poster:
            posters.setdefault(task_id, poster)
    phase4 = _read_json(run_dir / "phase4" / "build-manifest.json")
    for item in (phase4 or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        poster = _resolve(item.get("poster")) or _resolve(item.get("master"))
        if task_id and poster and task_id not in posters:
            posters[task_id] = poster
    return posters


def _load_from_prematch_runs(
    prematch_root: Path, image2_database: Path, target_date: date
) -> list[Task1Fixture]:
    fixtures_by_id: dict[str, Task1Fixture] = {}
    for run_dir in _prematch_run_dirs(prematch_root, target_date):
        fixtures_payload = _read_json(run_dir / "phase1" / "selected-fixtures.json")
        if fixtures_payload is None:
            fixtures_payload = _read_json(run_dir / "phase1" / "fixtures.json")
        if not fixtures_payload:
            continue
        posters = _prematch_posters(run_dir)
        source_manifest = run_dir / "phase1" / "selected-fixtures.json"
        if not source_manifest.is_file():
            source_manifest = run_dir / "phase1" / "fixtures.json"
        for entry in fixtures_payload.get("fixtures") or []:
            if not isinstance(entry, dict):
                continue
            fixture_id = str(entry.get("fixture_id") or "").strip()
            if not fixture_id:
                continue
            match_date = str(entry.get("schedule_date") or "").strip()
            if match_date != target_date.isoformat():
                continue
            home = str(entry.get("home_team") or "").strip()
            away = str(entry.get("away_team") or "").strip()
            competition = str(entry.get("competition") or "").strip()
            if not (home and away and competition):
                continue
            kickoff_brt = _normalize_kickoff(entry.get("kickoff_at_brt"))
            if not kickoff_brt:
                continue
            poster = posters.get(fixture_id, "")
            if not poster:
                # Lenient match for prefixed task ids (e.g. "r3-<fixture_id>")
                # that some pre-match runs write into poster-manifest.json.
                poster = next(
                    (
                        value
                        for key, value in posters.items()
                        if key == fixture_id or key.endswith("-" + fixture_id) or fixture_id in key
                    ),
                    "",
                )
            if not poster or not Path(poster).is_file():
                continue
            home_crest = _resolve_crest(image2_database, home)
            away_crest = _resolve_crest(image2_database, away)
            if not home_crest or not Path(home_crest).is_file():
                continue
            if not away_crest or not Path(away_crest).is_file():
                continue
            # Later run directories (higher batch / revision) supersede earlier ones
            # for the same fixture id.
            fixtures_by_id[fixture_id] = Task1Fixture(
                task1_fixture_id=fixture_id,
                source_fixture_id=None,
                composite_key=_composite_key(match_date, competition, home, away),
                competition=competition,
                home_team=home,
                away_team=away,
                match_date=match_date,
                kickoff_brt=kickoff_brt,
                channels=_channels(entry.get("channels")),
                home_crest=home_crest,
                away_crest=away_crest,
                source_manifest=str(source_manifest.resolve()),
                pre_match_poster=poster,
            )
    fixtures = list(fixtures_by_id.values())
    return sorted(fixtures, key=lambda fixture: (fixture.kickoff_brt, fixture.task1_fixture_id))


def _load_from_legacy_image2_outputs(image2_database: Path, target_date: date) -> list[Task1Fixture]:
    yymmdd = target_date.strftime("%y%m%d")
    production_dir = image2_database / "outputs" / f"{yymmdd}_prematch_image2"
    if not production_dir.is_dir():
        raise FileNotFoundError(f"Task 1 production directory is missing: {production_dir}")

    fixtures: list[Task1Fixture] = []
    for manifest_path in sorted(production_dir.glob("production_manifest_*.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        match = payload.get("match")
        if not isinstance(match, dict) or not match.get("match_id"):
            continue
        match_date = parse_pt_br_manifest_date(str(match.get("brasilia_date") or ""))
        if match_date != target_date.isoformat():
            continue
        channels = [
            value.strip()
            for value in str(match.get("channels") or "").split("•")
            if value.strip()
        ]
        assets = payload.get("assets") or {}
        generation = payload.get("generation") or {}
        home = str(match.get("home") or "").strip()
        away = str(match.get("away") or "").strip()
        competition = str(match.get("competition") or "").strip()
        task1_id = str(match["match_id"]).strip()
        fixture = Task1Fixture(
            task1_fixture_id=task1_id,
            source_fixture_id=None,
            composite_key=_composite_key(match_date, competition, home, away),
            competition=competition,
            home_team=home,
            away_team=away,
            match_date=match_date,
            kickoff_brt=parse_manifest_time(str(match.get("brasilia_time") or "")),
            channels=channels,
            home_crest=str(assets.get("home_crest") or ""),
            away_crest=str(assets.get("away_crest") or ""),
            source_manifest=str(manifest_path.resolve()),
            pre_match_poster=str(generation.get("final_poster") or ""),
        )
        for required in (fixture.home_crest, fixture.away_crest, fixture.pre_match_poster):
            if not required or not Path(required).is_file():
                raise FileNotFoundError(f"Task 1 artifact is missing for {task1_id}: {required}")
        fixtures.append(fixture)

    if not fixtures:
        raise RuntimeError(f"Task 1 has no selected fixture manifests for {target_date.isoformat()}")
    ids = [fixture.task1_fixture_id for fixture in fixtures]
    if len(ids) != len(set(ids)):
        raise RuntimeError("Task 1 contains duplicate match IDs")
    return sorted(fixtures, key=lambda fixture: (fixture.kickoff_brt, fixture.task1_fixture_id))
