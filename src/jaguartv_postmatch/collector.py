from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .task1 import Task1Fixture
from .util import canonical_team, normalize_name


LEAGUE_SLUGS = {
    "premier league": "eng.1",
    "la liga": "esp.1",
    "brasileirao serie a": "bra.1",
    "serie a rodada 2": "ita.1",
}
FINAL_STATUS_NAMES = {
    "STATUS_FULL_TIME": "FT",
    "STATUS_FINAL": "FT",
    "STATUS_END_OF_EXTRA_TIME": "AET",
    "STATUS_END_OF_PENALTIES": "PEN",
}


@dataclass(frozen=True)
class SourceCard:
    competition: str
    kickoff_brt: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    original_result_text: str


def parse_result_card_text(text: str, home_team: str, away_team: str) -> SourceCard:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    home_index = next(
        (index for index, line in enumerate(lines) if canonical_team(line) == canonical_team(home_team)),
        None,
    )
    away_index = next(
        (index for index, line in enumerate(lines) if canonical_team(line) == canonical_team(away_team)),
        None,
    )
    if home_index is None or away_index is None or away_index <= home_index:
        raise ValueError(f"source card teams do not match Task 1: {home_team} x {away_team}")
    between = lines[home_index + 1 : away_index]
    score_tokens = [int(value) for value in between if re.fullmatch(r"\d{1,2}", value)]
    if len(score_tokens) != 2 or not any(value.casefold() in {"x", "vs"} for value in between):
        raise ValueError(f"source card does not contain one final score: {between!r}")
    kickoff_values = [value for value in lines[:home_index] if re.fullmatch(r"\d{2}:\d{2}", value)]
    if len(kickoff_values) != 1:
        raise ValueError("source card kickoff is missing or ambiguous")
    return SourceCard(
        competition=lines[0],
        kickoff_brt=kickoff_values[0],
        home_team=lines[home_index],
        away_team=lines[away_index],
        home_score=score_tokens[0],
        away_score=score_tokens[1],
        original_result_text=text.strip(),
    )


def _league_slug(competition: str) -> str:
    normalized = normalize_name(competition)
    if normalized.startswith("premier league"):
        return "eng.1"
    if normalized.startswith("la liga"):
        return "esp.1"
    if normalized.startswith("brasileirao serie a"):
        return "bra.1"
    if normalized.startswith("serie a"):
        return "ita.1"
    raise ValueError(f"no official status adapter for competition: {competition}")


def _fixture_status_slugs(fixture: Task1Fixture) -> set[str]:
    slugs: set[str] = set()
    manifest_path = Path(fixture.source_manifest)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        fact_sources = ((manifest.get("match") or {}).get("fact_sources")) or []
    except (OSError, json.JSONDecodeError):
        fact_sources = []
    for source in fact_sources:
        match = re.search(r"/soccer/([a-z0-9.]+)/scoreboard", str(source or ""))
        if match:
            slugs.add(match.group(1))
    if not slugs:
        try:
            slugs.add(_league_slug(fixture.competition))
        except ValueError:
            pass
    return slugs


def _read_json(url: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            "30",
            "--header",
            "Accept: application/json",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid JSON response from {url}")
    return payload


def fetch_scoreboards(fixtures: list[Task1Fixture], target_date: date) -> dict[str, dict[str, Any]]:
    scoreboards: dict[str, dict[str, Any]] = {}
    slugs = sorted({slug for fixture in fixtures for slug in _fixture_status_slugs(fixture)})
    for slug in slugs:
        url = (
            "https://site.api.espn.com/apis/site/v2/sports/soccer/"
            f"{slug}/scoreboard?dates={target_date.strftime('%Y%m%d')}"
        )
        scoreboards[slug] = {"url": url, "payload": _read_json(url)}
    return scoreboards


def _event_teams(event: dict[str, Any]) -> dict[str, dict[str, Any]]:
    competitors = ((event.get("competitions") or [{}])[0].get("competitors") or [])
    return {
        str(competitor.get("homeAway") or ""): competitor
        for competitor in competitors
        if isinstance(competitor, dict)
    }


def match_status_event(
    fixture: Task1Fixture,
    scoreboards: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    candidates = []
    for slug, entry in scoreboards.items():
        for event in entry["payload"].get("events") or []:
            teams = _event_teams(event)
            home = ((teams.get("home") or {}).get("team") or {}).get("displayName") or ""
            away = ((teams.get("away") or {}).get("team") or {}).get("displayName") or ""
            if canonical_team(home) == canonical_team(fixture.home_team) and canonical_team(away) == canonical_team(fixture.away_team):
                candidates.append((event, str(entry["url"]), slug))
    if len(candidates) != 1:
        raise RuntimeError(
            f"official status event is not unique for {fixture.task1_fixture_id}: {len(candidates)}"
        )
    return candidates[0][0], candidates[0][1]


def _event_result(event: dict[str, Any]) -> dict[str, Any]:
    status = event.get("status") or {}
    status_type = status.get("type") or {}
    status_name = str(status_type.get("name") or "")
    if not status_type.get("completed") or status_name not in FINAL_STATUS_NAMES:
        return {"completed": False, "provider_status": status_name}
    teams = _event_teams(event)
    home = teams["home"]
    away = teams["away"]
    home_shootout = home.get("shootoutScore")
    away_shootout = away.get("shootoutScore")
    result_status = "PEN" if home_shootout is not None or away_shootout is not None else FINAL_STATUS_NAMES[status_name]
    competition = (event.get("competitions") or [{}])[0]
    summary_url = next(
        (
            link.get("href")
            for link in event.get("links") or []
            if "summary" in (link.get("rel") or []) and link.get("href")
        ),
        "",
    )
    return {
        "completed": True,
        "result_status": result_status,
        "provider_status": status_name,
        "provider_match_id": str(event.get("id") or ""),
        "provider_event_url": summary_url,
        "home_score": int(home.get("score")),
        "away_score": int(away.get("score")),
        "extra_time": competition.get("details") if result_status == "AET" else None,
        "penalty_shootout": (
            {"home": int(home_shootout), "away": int(away_shootout)}
            if result_status == "PEN"
            else None
        ),
    }


async def scrape_yesterday_cards(
    collector_root: Path,
    source_url: str,
    fixtures: list[Task1Fixture],
) -> tuple[list[SourceCard], str]:
    sys.path.insert(0, str(collector_root / "src"))
    from tomorrow_fixtures.collection.browser import BrowserSession
    from tomorrow_fixtures.collection.page import activate_date, activate_featured, fixture_cards, verify_source_page
    from tomorrow_fixtures.config import Settings

    settings = Settings(source_url=source_url)
    selected: list[SourceCard] = []
    async with BrowserSession(settings) as page:
        source_updated_text = await verify_source_page(page, source_url)
        await activate_date(page, "Ontem")
        await activate_featured(page)
        cards = await fixture_cards(page)
        raw_cards = [(await card.inner_text()).strip() for card in cards]
        for fixture in fixtures:
            matches: list[SourceCard] = []
            for raw in raw_cards:
                try:
                    matches.append(parse_result_card_text(raw, fixture.home_team, fixture.away_team))
                except ValueError:
                    continue
            if len(matches) != 1:
                raise RuntimeError(
                    f"copa.jarg.top result card is not unique for {fixture.task1_fixture_id}: {len(matches)}"
                )
            selected.append(matches[0])
    return selected, source_updated_text


def collect_completed_results(
    collector_root: Path,
    source_url: str,
    fixtures: list[Task1Fixture],
    target_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cards, source_updated_text = asyncio.run(
        scrape_yesterday_cards(collector_root, source_url, fixtures)
    )
    cards_by_pair = {
        (canonical_team(card.home_team), canonical_team(card.away_team)): card for card in cards
    }
    scoreboards = fetch_scoreboards(fixtures, target_date)
    retrieved_at = datetime.now(timezone.utc).isoformat()
    completed: list[dict[str, Any]] = []
    unfinished: list[dict[str, Any]] = []
    for fixture in fixtures:
        card = cards_by_pair[(canonical_team(fixture.home_team), canonical_team(fixture.away_team))]
        provider: dict[str, Any]
        status_source_url = source_url
        fixture_slugs = _fixture_status_slugs(fixture)
        if fixture_slugs:
            event, status_source_url = match_status_event(fixture, scoreboards)
            provider = _event_result(event)
        else:
            provider = {
                "completed": True,
                "result_status": "FT",
                "provider_status": "SOURCE_CARD_FINAL",
                "provider_match_id": fixture.task1_fixture_id,
                "provider_event_url": "",
                "home_score": card.home_score,
                "away_score": card.away_score,
                "extra_time": None,
                "penalty_shootout": None,
            }
        base = {
            "task1_fixture_id": fixture.task1_fixture_id,
            "source_fixture_id": fixture.source_fixture_id,
            "composite_key": fixture.composite_key,
            "competition": fixture.competition,
            "home_team": fixture.home_team,
            "away_team": fixture.away_team,
            "official_match_date": fixture.match_date,
            "original_kickoff_time": fixture.kickoff_brt,
            "public_timezone_label": "Horário de Brasília",
            "channels": fixture.channels,
            "official_source_url": source_url,
            "status_verification_url": status_source_url,
            "retrieval_timestamp": retrieved_at,
            "source_updated_text": source_updated_text,
            "original_result_text": card.original_result_text,
            "task1_manifest": fixture.source_manifest,
            "task1_pre_match_poster": fixture.pre_match_poster,
            **provider,
        }
        if not provider["completed"]:
            unfinished.append(base)
            continue
        if (
            fixture_slugs
            and (card.home_score, card.away_score) != (provider["home_score"], provider["away_score"])
        ):
            raise RuntimeError(
                f"source score mismatch for {fixture.task1_fixture_id}: "
                f"copa={card.home_score}-{card.away_score}, status={provider['home_score']}-{provider['away_score']}"
            )
        completed.append(base)
    return completed, unfinished
