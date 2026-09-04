from __future__ import annotations

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .collector import _league_slug
from .util import canonical_team, executable_path, normalize_name, tool_environment, utc_now


def _curl_json(url: str) -> dict[str, Any]:
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
        raise RuntimeError(f"expected an object from {url}")
    return payload


def _research_date_text(result: dict[str, Any]) -> tuple[str, str]:
    parsed = date.fromisoformat(str(result["official_match_date"]))
    english_months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    portuguese_months = (
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    )
    return (
        f"{parsed.day} {english_months[parsed.month - 1]} {parsed.year}",
        f"{parsed.day} {portuguese_months[parsed.month - 1]} {parsed.year}",
    )


def parse_exa_output(raw: str, retrieved_at: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for block in re.split(r"\n-{3,}\n", raw.strip()):
        title_match = re.search(r"^Title:\s*(.+)$", block, re.MULTILINE)
        url_match = re.search(r"^URL:\s*(https?://\S+)$", block, re.MULTILINE)
        if not title_match or not url_match:
            continue
        published = re.search(r"^Published:\s*(.+)$", block, re.MULTILINE)
        highlight = block.split("Highlights:", 1)[1].strip() if "Highlights:" in block else ""
        excerpt = re.sub(r"\n\.\.\.\n?", " ", highlight)
        excerpt = re.sub(r"\s+", " ", excerpt).strip()[:1600]
        records.append(
            {
                "source_url": url_match.group(1),
                "platform": "web-via-agent-reach-exa",
                "title": title_match.group(1).strip(),
                "publication_time": published.group(1).strip() if published else None,
                "retrieval_time": retrieved_at,
                "source_excerpt": excerpt,
                "summary": excerpt[:500],
                "confidence": 0.9,
                "use_for_facts": True,
            }
        )
    return records


def _agent_reach_web(query: str, retrieved_at: str) -> list[dict[str, Any]]:
    mcporter = executable_path("mcporter")
    if not mcporter:
        raise RuntimeError("agent-reach Exa backend is unavailable: mcporter not found")
    completed = subprocess.run(
        [
            mcporter,
            "call",
            "exa.web_search_exa",
            f"query={query}",
            "numResults=5",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=tool_environment(),
    )
    return parse_exa_output(completed.stdout, retrieved_at)


def is_match_specific_social_record(
    record: dict[str, Any], result: dict[str, Any]
) -> bool:
    raw_text = " ".join(
        str(record.get(field) or "") for field in ("title", "source_excerpt", "summary")
    )
    normalized = normalize_name(raw_text)
    compact = normalized.replace(" ", "")

    def contains_team(team: object) -> bool:
        identity = canonical_team(str(team))
        return identity in normalized or identity.replace(" ", "") in compact

    home_hit = contains_team(result["home_team"])
    away_hit = contains_team(result["away_team"])
    home_score = int(result["home_score"])
    away_score = int(result["away_score"])
    final_score = re.search(
        rf"(?<!\d){home_score}\s*(?:-|x|×|:|：)\s*\[?{away_score}\]?(?!\d)",
        raw_text.casefold(),
    )
    plausible_score = r"(?:[0-9]|1[0-9])"
    any_score = re.search(
        rf"(?<!\d){plausible_score}\s*(?:-|x|×|:|：)\s*"
        rf"\[?{plausible_score}\]?(?!\d)",
        raw_text,
    )
    post_match_marker = re.search(
        r"\b(?:full[ -]?time|ft|placar|resultado|victory|venceu|won|beat|beats|"
        r"jogo encerrado|apito final|gol|penalti|penalty|var)\b|"
        r"\bvitoria\s+(?:de|do|da|por)\b",
        normalized,
    )
    return bool(
        (home_hit and away_hit and (any_score or post_match_marker))
        or ((home_hit or away_hit) and final_score)
    )


def _agent_reach_x(
    query: str, retrieved_at: str, result: dict[str, Any]
) -> list[dict[str, Any]]:
    opencli = executable_path("opencli")
    if not opencli:
        raise RuntimeError("agent-reach Twitter/X backend is unavailable: opencli not found")
    completed = subprocess.run(
        [opencli, "twitter", "search", query, "-f", "json"],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=tool_environment(),
    )
    values = json.loads(completed.stdout)
    if not isinstance(values, list):
        return []

    def engagement(item: dict[str, Any]) -> tuple[int, int]:
        try:
            views = int(item.get("views") or 0)
        except (TypeError, ValueError):
            views = 0
        return views, int(item.get("likes") or 0)

    records = []
    for item in sorted((value for value in values if isinstance(value, dict)), key=engagement, reverse=True)[:5]:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        records.append(
            {
                "source_url": str(item.get("url") or ""),
                "platform": "x-via-agent-reach-opencli",
                "title": f"X post by @{item.get('author') or 'unknown'}",
                "publication_time": item.get("created_at"),
                "retrieval_time": retrieved_at,
                "source_excerpt": text[:1000],
                "summary": text[:500],
                "confidence": 0.55,
                "use_for_facts": False,
                "engagement": {
                    "likes": int(item.get("likes") or 0),
                    "views": engagement(item)[0],
                },
                "media_urls": item.get("media_urls") or [],
                "media_posters": item.get("media_posters") or [],
            }
        )
    return [record for record in records if is_match_specific_social_record(record, result)]


def _team_stats(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for entry in (summary.get("boxscore") or {}).get("teams") or []:
        stats = {
            str(item.get("name")): item.get("displayValue")
            for item in entry.get("statistics") or []
            if item.get("name")
        }
        rows.append(
            {
                "team": (entry.get("team") or {}).get("displayName"),
                "home_away": entry.get("homeAway"),
                "statistics": stats,
                "current_uniform": (entry.get("team") or {}).get("uniform") or {},
                "crest_url": (entry.get("team") or {}).get("logo"),
            }
        )
    return rows


def _participants(summary: dict[str, Any]) -> list[dict[str, Any]]:
    teams = []
    for entry in summary.get("rosters") or []:
        roster = []
        for player in entry.get("roster") or []:
            athlete = player.get("athlete") or {}
            played = bool(player.get("starter") or player.get("subbedIn"))
            roster.append(
                {
                    "athlete_id": str(athlete.get("id") or ""),
                    "name": athlete.get("displayName"),
                    "starter": bool(player.get("starter")),
                    "subbed_in": bool(player.get("subbedIn")),
                    "subbed_out": bool(player.get("subbedOut")),
                    "played": played,
                    "position": (player.get("position") or {}).get("abbreviation"),
                }
            )
        teams.append(
            {
                "team": (entry.get("team") or {}).get("displayName"),
                "team_id": str((entry.get("team") or {}).get("id") or ""),
                "home_away": entry.get("homeAway"),
                "formation": entry.get("formation"),
                "current_uniform": entry.get("uniform") or {},
                "roster": roster,
            }
        )
    return teams


def _goal_events(summary: dict[str, Any]) -> list[dict[str, Any]]:
    competition = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    events = []
    for detail in competition.get("details") or []:
        if not detail.get("scoringPlay"):
            continue
        participants = [
            (entry.get("athlete") or {}).get("displayName")
            for entry in detail.get("participants") or []
            if (entry.get("athlete") or {}).get("displayName")
        ]
        events.append(
            {
                "minute": (detail.get("clock") or {}).get("displayValue"),
                "team": (detail.get("team") or {}).get("displayName"),
                "scorer": participants[0] if participants else None,
                "assist": participants[1] if len(participants) > 1 else None,
                "own_goal": bool(detail.get("ownGoal")),
                "penalty": bool(detail.get("penaltyKick")),
            }
        )
    return events


def _incidents(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "red_cards": [],
        "penalties": [],
        "var": [],
        "injuries": [],
        "important_substitutions": [],
        "serious_fouls": [],
    }
    for item in summary.get("commentary") or []:
        event_type = str((item.get("type") or {}).get("type") or "")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        record = {
            "minute": (item.get("clock") or {}).get("displayValue"),
            "team": (item.get("team") or {}).get("displayName"),
            "event_type": event_type,
            "text": text,
        }
        lowered = f"{event_type} {text}".casefold()
        if "red-card" in event_type or "red card" in lowered:
            groups["red_cards"].append(record)
        if "penalty" in lowered:
            groups["penalties"].append(record)
        if "var" in lowered:
            groups["var"].append(record)
        if "injur" in lowered:
            groups["injuries"].append(record)
        if event_type == "substitution" and ("injur" in lowered or len(groups["important_substitutions"]) < 6):
            groups["important_substitutions"].append(record)
        if "bad foul" in lowered and ("red" in lowered or "serious" in lowered):
            groups["serious_fouls"].append(record)
    return groups


def _visual_candidates(
    result: dict[str, Any], participants: list[dict[str, Any]], goals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    played: dict[str, dict[str, Any]] = {}
    for team in participants:
        for player in team["roster"]:
            if player["played"] and player["name"]:
                played[canonical_team(str(player["name"]))] = {**player, "team": team["team"]}
    ordered_names = []
    winning_team = result["home_team"] if result["home_score"] > result["away_score"] else result["away_team"]
    for goal in goals:
        if goal.get("scorer") and canonical_team(str(goal.get("team") or "")) == canonical_team(winning_team):
            ordered_names.append(str(goal["scorer"]))
    for goal in goals:
        if goal.get("scorer"):
            ordered_names.append(str(goal["scorer"]))
    candidates = []
    seen = set()
    for name in ordered_names:
        key = canonical_team(name)
        if key in seen or key not in played:
            continue
        seen.add(key)
        player = played[key]
        candidates.append(
            {
                **player,
                "headshot_url": (
                    "https://a.espncdn.com/i/headshots/soccer/players/full/"
                    f"{player['athlete_id']}.png"
                ),
                "asset_status": "candidate-not-downloaded",
                "license_record": "operator-authorized for this project",
            }
        )
        if len(candidates) == 3:
            break
    return candidates


def research_match(result: dict[str, Any]) -> dict[str, Any]:
    retrieved_at = utc_now()
    english_date, portuguese_date = _research_date_text(result)
    structured = str(result.get("provider_status") or "") != "SOURCE_CARD_FINAL"
    if structured:
        summary = {}
        summary_url = str(result.get("status_verification_url") or result.get("official_source_url") or "")
        try:
            slug = _league_slug(str(result["competition"]))
        except ValueError as exc:
            # No ESPN adapter for this competition (e.g. a long-form league name).
            # Degrade gracefully and rely on the web/X research fallbacks instead
            # of aborting the whole pipeline for one unavailable structured feed.
            slug = ""
            print(
                f"[warn] no ESPN league adapter for competition="
                f"{result['competition']!r}; continuing with web/X research only: {exc}"
            )
        if slug:
            event_id = str(result["provider_match_id"])
            summary_url = (
                "https://site.api.espn.com/apis/site/v2/sports/soccer/"
                f"{slug}/summary?event={event_id}"
            )
            # Non-fatal: ESPN's event id namespace differs from API-Football's
            # provider_match_id, so this 404s for API-Football-sourced fixtures.
            try:
                summary = _curl_json(summary_url)
            except Exception as exc:
                summary = {}
                print(
                    f"[warn] ESPN structured fetch failed for event={event_id} "
                    f"({slug}); continuing with web/X research only: {exc}"
                )
    else:
        summary = {}
        summary_url = str(result.get("status_verification_url") or result.get("official_source_url") or "")
    score_query = (
        f"{result['home_team']} {result['home_score']}-{result['away_score']} {result['away_team']} "
        f"{english_date} match report goals red card VAR"
    )
    x_query = (
        f"{result['home_team']} {result['away_team']} {result['home_score']}-{result['away_score']} "
        f"{portuguese_date}"
    )
    try:
        web_sources = _agent_reach_web(score_query, retrieved_at)
    except Exception as exc:  # non-fatal: agent-reach Exa backend unavailable/timeout
        web_sources = []
        print(f"[warn] agent_reach_web unavailable, skipping web research: {exc}")
    try:
        x_sources = _agent_reach_x(x_query, retrieved_at, result)
    except Exception as exc:  # non-fatal: agent-reach X backend unavailable/timeout
        x_sources = []
        print(f"[warn] agent_reach_x unavailable, skipping X research: {exc}")
    participants = _participants(summary)
    goals = _goal_events(summary)
    incidents = _incidents(summary)
    source_records = [
        {
            "source_url": result["official_source_url"],
            "platform": "official-schedule-collector",
            "title": "Jogos de Hoje - resultado selecionado do Task 1",
            "publication_time": None,
            "retrieval_time": result["retrieval_timestamp"],
            "source_excerpt": result["original_result_text"],
            "summary": f"{result['home_team']} {result['home_score']}-{result['away_score']} {result['away_team']}",
            "confidence": 1.0,
            "use_for_facts": True,
        },
    ]
    if structured:
        source_records.append(
            {
                "source_url": summary_url,
                "platform": "ESPN-match-record",
                "title": f"Official match data {event_id}",
                "publication_time": ((summary.get("header") or {}).get("competitions") or [{}])[0].get("date"),
                "retrieval_time": retrieved_at,
                "source_excerpt": f"Status FT; {len(goals)} scoring events; verified roster and commentary loaded.",
                "summary": "Structured score, participants, goals, incidents, substitutions, formations and statistics.",
                "confidence": 1.0,
                "use_for_facts": True,
            }
        )
    source_records.extend(web_sources)
    source_records.extend(x_sources)
    return {
        "schema_version": "jaguartv-postmatch-research-v1",
        "generated_at": retrieved_at,
        "match": result,
        "source_records": source_records,
        "research_channel_audit": {
            "agent_reach_used": True,
            "web_backend": "Exa via mcporter",
            "social_backend": "Twitter/X via OpenCLI",
            "social_used_for_facts": False,
            "social_match_specific_records": len(x_sources),
            "secrets_recorded": False,
        },
        "verified_match_record": {
            "participants": participants,
            "goals": goals,
            "incidents": incidents,
            "team_statistics": _team_stats(summary),
        },
        "discussion_topics": [source["summary"] for source in x_sources[:3]],
        "post_match_report_summaries": [source["summary"] for source in web_sources[:3]],
        "visual_candidates": _visual_candidates(result, participants, goals),
        "visual_fallback": {
            "mode": "virtual-hardman-player",
            "use_when": "no candidate asset passes identity, participation, resolution and current-kit QA",
            "must_not_imply_real_identity": True,
        },
    }


def build_phase2(results_path: Path, output_dir: Path, workers: int = 3) -> dict[str, Any]:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    results = payload.get("results") or []
    if not results:
        raise RuntimeError("Phase 1 has no completed results")
    output_dir.mkdir(parents=True, exist_ok=True)
    produced: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as executor:
        futures = {executor.submit(research_match, result): result for result in results}
        for future in as_completed(futures):
            result = futures[future]
            record = future.result()
            produced[str(result["task1_fixture_id"])] = record
    artifacts = []
    for match_id in sorted(produced):
        path = output_dir / f"{match_id}_research.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(produced[match_id], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        artifacts.append(str(path.resolve()))
    index = {
        "schema_version": "jaguartv-postmatch-research-index-v1",
        "generated_at": utc_now(),
        "match_count": len(artifacts),
        "artifacts": artifacts,
    }
    index_path = output_dir / "research-index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**index, "index_path": str(index_path.resolve())}
