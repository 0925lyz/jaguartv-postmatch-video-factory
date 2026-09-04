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

from .credentials import env_or_keychain
from .task1 import Task1Fixture
from .util import canonical_team, normalize_name


API_FOOTBALL_ENDPOINT = "https://v3.football.api-sports.io/fixtures"
API_FOOTBALL_FINAL_STATES = {"FT", "AET", "PEN"}
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
    "STATUS_FINAL_PEN": "PEN",
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
    if normalized.startswith("brasileirao"):
        return "bra.1"
    if normalized.startswith("campeonato brasileiro"):
        if "serie b" in normalized:
            return "bra.2"
        return "bra.1"
    if normalized.startswith("copa do brasil"):
        return "bra.copa_do_brazil"
    if normalized.startswith("copa argentina"):
        return "arg.copa"
    if normalized.startswith("serie b"):
        return "bra.2"
    if normalized.startswith("bundesliga"):
        return "aut.1"
    if normalized.startswith("championship"):
        return "eng.2"
    if normalized.startswith("primera division"):
        return "per.1"
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
        match = re.search(r"/soccer/([a-z0-9._]+)/scoreboard", str(source or ""))
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


def _read_api_football(url: str, api_key: str) -> dict[str, Any]:
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
            "--header",
            f"x-apisports-key: {api_key}",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("invalid JSON response from API-Football")
    return payload


def fetch_api_football_results(
    target_date: date,
    key_name: str,
    endpoint: str = API_FOOTBALL_ENDPOINT,
    required: bool = False,
) -> dict[str, Any]:
    try:
        api_key = env_or_keychain(key_name)
    except Exception as error:
        if required:
            raise RuntimeError(f"API-Football credential unavailable: {key_name}") from error
        return {"url": "", "events": [], "available": False}
    url = f"{endpoint}?date={target_date.isoformat()}&timezone=America/Sao_Paulo"
    payload = _read_api_football(url, api_key)
    return {"url": url, "events": payload.get("response") or [], "available": True}


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
            if _team_match(fixture.home_team, home) and _team_match(fixture.away_team, away):
                candidates.append((event, str(entry["url"]), slug))
    if len(candidates) != 1:
        raise RuntimeError(
            f"official status event is not unique for {fixture.task1_fixture_id}: {len(candidates)}"
        )
    return candidates[0][0], candidates[0][1]


def _team_match(fixture_team: str, provider_team: str) -> bool:
    """Loose team-name equality tolerant of provider name suffixes.

    API-Football/ESPN often append the city/state to a club name (e.g.
    "Nautico Recife" vs the fixture's "Náutico"). Accept an exact canonical match
    or a token-subset relation in either direction, so the shorter fixture name
    still links to the provider's longer one. Callers match the full (home, away)
    pair, which keeps this safe against coincidental single-side overlaps.
    """
    cf = canonical_team(fixture_team)
    cp = canonical_team(provider_team)
    if cf == cp:
        return True
    tf = set(cf.split())
    tp = set(cp.split())
    return bool(tf) and bool(tp) and (tf <= tp or tp <= tf)


def match_api_football_event(fixture: Task1Fixture, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    fixture_id = str(fixture.source_fixture_id or "")
    for event in events:
        api_fixture_id = str(((event.get("fixture") or {}).get("id")) or "")
        if fixture_id and fixture_id == api_fixture_id:
            return event
    candidates = []
    for event in events:
        teams = event.get("teams") or {}
        home = ((teams.get("home") or {}).get("name")) or ""
        away = ((teams.get("away") or {}).get("name")) or ""
        if _team_match(fixture.home_team, home) and _team_match(fixture.away_team, away):
            candidates.append(event)
    if len(candidates) > 1:
        raise RuntimeError(f"API-Football event is not unique for {fixture.task1_fixture_id}: {len(candidates)}")
    return candidates[0] if candidates else None


def _api_football_event_result(event: dict[str, Any]) -> dict[str, Any]:
    fixture = event.get("fixture") or {}
    status = fixture.get("status") or {}
    status_short = str(status.get("short") or "")
    if status_short not in API_FOOTBALL_FINAL_STATES:
        return {"completed": False, "provider_status": status_short or "UNKNOWN"}
    goals = event.get("goals") or {}
    home_score = goals.get("home")
    away_score = goals.get("away")
    if home_score is None or away_score is None:
        return {"completed": False, "provider_status": f"{status_short}_NO_SCORE"}
    score = event.get("score") or {}
    extratime = score.get("extratime") or {}
    penalty = score.get("penalty") or {}
    result_status = "PEN" if penalty.get("home") is not None or penalty.get("away") is not None else status_short
    return {
        "completed": True,
        "result_status": result_status,
        "provider_status": status_short,
        "provider_name": "api-football",
        "provider_match_id": str(fixture.get("id") or ""),
        "provider_event_url": "",
        "home_score": int(home_score),
        "away_score": int(away_score),
        "extra_time": (
            {"home": int(extratime["home"]), "away": int(extratime["away"])}
            if extratime.get("home") is not None and extratime.get("away") is not None
            else None
        ),
        "penalty_shootout": (
            {"home": int(penalty["home"]), "away": int(penalty["away"])}
            if result_status == "PEN"
            else None
        ),
    }


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
        "provider_name": "espn",
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
) -> tuple[list[SourceCard], list[str], list[str], str]:
    """Return ``(selected_cards, missing_fixture_ids, ambiguous_fixture_ids, source_updated_text)``.

    copa.jarg.top is a *cross-check* source (API-Football is the primary result
    source, per AGENTS.md). A fixture that yields 0 cards is reported as ``missing``
    and processed from the authoritative provider instead of crashing the whole run.
    A fixture that yields more than one card is reported as ``ambiguous`` (first
    match kept) so the operator can review it without blocking production.
    """
    sys.path.insert(0, str(collector_root / "src"))
    from tomorrow_fixtures.collection.browser import BrowserSession
    from tomorrow_fixtures.collection.page import activate_date, activate_featured, fixture_cards, verify_source_page
    from tomorrow_fixtures.config import Settings

    settings = Settings(source_url=source_url)
    selected: list[SourceCard] = []
    missing: list[str] = []
    ambiguous: list[str] = []
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
            if len(matches) == 1:
                selected.append(matches[0])
            elif len(matches) == 0:
                missing.append(fixture.task1_fixture_id)
            else:
                ambiguous.append(fixture.task1_fixture_id)
                selected.append(matches[0])
    return selected, missing, ambiguous, source_updated_text


def collect_completed_results(
    collector_root: Path,
    source_url: str,
    fixtures: list[Task1Fixture],
    target_date: date,
    result_sources: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result_sources = result_sources or {}
    cards, missing_ids, ambiguous_ids, source_updated_text = asyncio.run(
        scrape_yesterday_cards(collector_root, source_url, fixtures)
    )
    ambiguous_set = set(ambiguous_ids)
    cards_by_pair = {
        (canonical_team(card.home_team), canonical_team(card.away_team)): card for card in cards
    }
    api_football = fetch_api_football_results(
        target_date,
        str(result_sources.get("api_football_key_env") or "API_FOOTBALL_KEY"),
        str(result_sources.get("api_football_endpoint") or API_FOOTBALL_ENDPOINT),
        bool(result_sources.get("api_football_required", False)),
    )
    scoreboards: dict[str, dict[str, Any]] | None = None
    retrieved_at = datetime.now(timezone.utc).isoformat()
    completed: list[dict[str, Any]] = []
    unfinished: list[dict[str, Any]] = []
    for fixture in fixtures:
        card = cards_by_pair.get(
            (canonical_team(fixture.home_team), canonical_team(fixture.away_team))
        )
        has_card = card is not None
        provider: dict[str, Any]
        status_source_url = source_url
        fixture_slugs = _fixture_status_slugs(fixture)
        api_event = match_api_football_event(fixture, api_football["events"])
        if api_event is not None:
            status_source_url = api_football["url"]
            provider = _api_football_event_result(api_event)
        elif fixture_slugs:
            if scoreboards is None:
                scoreboards = fetch_scoreboards(fixtures, target_date)
            event, status_source_url = match_status_event(fixture, scoreboards)
            provider = _event_result(event)
        elif has_card:
            provider = {
                "completed": True,
                "result_status": "FT",
                "provider_status": "SOURCE_CARD_FINAL",
                "provider_name": "copa-source-card",
                "provider_match_id": fixture.task1_fixture_id,
                "provider_event_url": "",
                "home_score": card.home_score,
                "away_score": card.away_score,
                "extra_time": None,
                "penalty_shootout": None,
            }
        else:
            # No source-page card AND no authoritative provider (API-Football /
            # status event). copa.jarg.top is only a cross-check, so without an
            # authoritative score we cannot safely finalize this fixture.
            unfinished.append(
                {
                    "task1_fixture_id": fixture.task1_fixture_id,
                    "competition": fixture.competition,
                    "home_team": fixture.home_team,
                    "away_team": fixture.away_team,
                    "reason": "no_source_card_and_no_provider",
                    "source_card_missing": True,
                }
            )
            continue
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
            "api_football_url": api_football["url"],
            "api_football_matched": api_event is not None,
            "retrieval_timestamp": retrieved_at,
            "source_updated_text": source_updated_text,
            "original_result_text": card.original_result_text if has_card else "",
            "source_card_missing": (not has_card),
            "source_card_ambiguous": fixture.task1_fixture_id in ambiguous_set,
            "task1_manifest": fixture.source_manifest,
            "task1_pre_match_poster": fixture.pre_match_poster,
            **provider,
        }
        if not provider["completed"]:
            unfinished.append(base)
            continue
        if (
            (api_event is not None or fixture_slugs)
            and has_card
            and (card.home_score, card.away_score) != (provider["home_score"], provider["away_score"])
        ):
            if "AO VIVO" in card.original_result_text.upper():
                # copa.jarg.top still lists the match as live (stale card); trust the
                # authoritative final score provider instead of failing closed.
                base["source_score_mismatch_warning"] = (
                    f"copa_live={card.home_score}-{card.away_score}, "
                    f"status={provider['home_score']}-{provider['away_score']} (trusted {provider['provider_name']} final)"
                )
            else:
                raise RuntimeError(
                    f"source score mismatch for {fixture.task1_fixture_id}: "
                    f"copa={card.home_score}-{card.away_score}, status={provider['home_score']}-{provider['away_score']}"
                )
        completed.append(base)
    return completed, unfinished
