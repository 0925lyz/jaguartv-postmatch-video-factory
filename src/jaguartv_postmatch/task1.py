from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

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


def load_task1_fixtures(image2_database: Path, target_date: date) -> list[Task1Fixture]:
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
