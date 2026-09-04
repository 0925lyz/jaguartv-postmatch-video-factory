from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from .credentials import env_or_keychain
from .util import canonical_team, parse_manifest_time, parse_pt_br_manifest_date, stable_json_hash


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


def _asset_path(value: object, roots: list[Path]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    if path.is_absolute() and path.is_file():
        return str(path.resolve())
    for root in roots:
        candidate = (root / path).resolve()
        if candidate.is_file():
            return str(candidate)
    return ""


def _find_crest(image2_database: Path, team: str) -> str:
    wanted = canonical_team(team)
    crests = image2_database / "assets" / "crests"
    matches = [
        path
        for path in crests.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp"}
        and canonical_team(path.stem) == wanted
    ]
    return str(sorted(matches)[-1].resolve()) if matches else ""


def _download_crest(url: str, team: str, image2_database: Path, yymmdd: str) -> str:
    if not url.startswith(("https://", "http://")):
        return ""
    target_dir = image2_database / "assets" / "crests" / yymmdd
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    destination = target_dir / f"{canonical_team(team).replace(' ', '_').title()}{suffix}"
    request = urllib.request.Request(url, headers={"User-Agent": "JaguarTVPostmatch/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
    if not data.startswith((b"\x89PNG", b"\xff\xd8", b"RIFF")):
        raise ValueError(f"downloaded crest is not an image for {team}")
    destination.write_bytes(data)
    return str(destination.resolve())


def _api_football_logos(target_date: date) -> dict[tuple[str, str], tuple[str, str]]:
    try:
        api_key = env_or_keychain("API_FOOTBALL_KEY")
    except Exception:
        return {}
    url = f"https://v3.football.api-sports.io/fixtures?date={target_date.isoformat()}&timezone=America/Sao_Paulo"
    request = urllib.request.Request(url, headers={"x-apisports-key": api_key, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    logos: dict[tuple[str, str], tuple[str, str]] = {}
    for event in payload.get("response") or []:
        teams = event.get("teams") or {}
        home, away = teams.get("home") or {}, teams.get("away") or {}
        key = (canonical_team(str(home.get("name") or "")), canonical_team(str(away.get("name") or "")))
        if all(key) and home.get("logo") and away.get("logo"):
            logos[key] = (str(home["logo"]), str(away["logo"]))
    return logos


def _resolve_crest(
    raw: object,
    team: str,
    image2_database: Path,
    yymmdd: str,
    roots: list[Path],
    api_logos: dict[tuple[str, str], tuple[str, str]],
    pair: tuple[str, str],
    side: int,
) -> str:
    return (
        _asset_path(raw, roots)
        or _find_crest(image2_database, team)
        or (
            _download_crest(api_logos[pair][side], team, image2_database, yymmdd)
            if pair in api_logos
            else ""
        )
    )


def load_task1_fixtures(image2_database: Path, target_date: date) -> list[Task1Fixture]:
    yymmdd = target_date.strftime("%y%m%d")
    production_dir = image2_database / "outputs" / f"{yymmdd}_prematch_image2"
    if not production_dir.is_dir():
        raise FileNotFoundError(f"Task 1 production directory is missing: {production_dir}")

    fixtures: list[Task1Fixture] = []
    api_logos: dict[tuple[str, str], tuple[str, str]] | None = None
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
        roots = [manifest_path.parent, production_dir, image2_database]
        pair = (canonical_team(home), canonical_team(away))
        api_logos = api_logos if api_logos is not None else _api_football_logos(target_date)
        home_crest = _resolve_crest(assets.get("home_crest"), home, image2_database, yymmdd, roots, api_logos, pair, 0)
        away_crest = _resolve_crest(assets.get("away_crest"), away, image2_database, yymmdd, roots, api_logos, pair, 1)
        final_poster = _asset_path(generation.get("final_poster"), roots)
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
            home_crest=home_crest,
            away_crest=away_crest,
            source_manifest=str(manifest_path.resolve()),
            pre_match_poster=final_poster,
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
