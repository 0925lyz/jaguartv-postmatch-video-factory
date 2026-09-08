from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .assets import materialize_player_assets
from .collector import collect_completed_results
from .lock import LockUnavailable, ProcessLock
from .phase3 import run_phase3
from .phase4 import run_phase4
from .phase5 import run_phase5
from .research import build_phase2
from .store import WorkflowStore
from .task1 import load_task1_fixtures
from .credentials import env_or_keychain
from .util import executable_path, tool_environment, utc_now


ROOT = Path(__file__).resolve().parents[2]
FACTORY_ROOT = ROOT
DEFAULT_CONFIG = FACTORY_ROOT / "config" / "local.json"


def _expand_config_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_config_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_config_value(item) for item in value]
    if isinstance(value, str):
        expanded = os.path.expandvars(value)
        if "${" in expanded:
            raise ValueError(f"unresolved environment variable in config value: {value}")
        return expanded
    return value


def load_config(path: Path) -> dict[str, Any]:
    config_path = path.expanduser().resolve()
    payload = _expand_config_value(json.loads(config_path.read_text(encoding="utf-8")))
    if not isinstance(payload, dict):
        raise ValueError("post-match config must be a JSON object")
    for key in (
        "collector_root", "system_prompts_and_models", "jaguartv_v7_pack",
        "image2_database", "figure_1", "channel_icons", "pending_review_uploader",
        "runtime_root",
    ):
        raw = Path(str(payload[key])).expanduser()
        payload[key] = str(raw if raw.is_absolute() else (FACTORY_ROOT / raw).resolve())
    reasoning = payload.get("reasoning") or {}
    if not str(reasoning.get("primary_model") or "current-task").strip():
        raise ValueError("primary text model must be current-task or a configured model name")
    server = payload.get("server") or {}
    if server.get("category") != "post_match_score" or server.get("label") != "赛后比分":
        raise ValueError("server target must be Pending Review label 赛后比分")
    return payload


def previous_brasilia_day() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date() - timedelta(days=1)


def _path(config: dict[str, Any], key: str) -> Path:
    return Path(str(config[key])).expanduser().resolve()


def _credential_available(name: str) -> bool:
    try:
        return bool(env_or_keychain(name))
    except Exception:
        return False


def _research_connectivity() -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    mcporter = executable_path("mcporter")
    if not mcporter:
        checks["agent_reach_exa"] = {"available": False, "error": "mcporter not found"}
    else:
        try:
            completed = subprocess.run(
                [mcporter, "call", "exa.web_search_exa", "query=football match result", "numResults=1"],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
                env=tool_environment(),
            )
            checks["agent_reach_exa"] = {
                "available": bool(re.search(r"^URL:\s*https?://", completed.stdout, re.MULTILINE)),
                "backend": "Exa via mcporter",
            }
        except (OSError, subprocess.SubprocessError) as error:
            checks["agent_reach_exa"] = {
                "available": False,
                "backend": "Exa via mcporter",
                "error": f"{type(error).__name__}: connectivity probe failed",
            }

    opencli = executable_path("opencli")
    if not opencli:
        checks["agent_reach_x"] = {"available": False, "error": "opencli not found"}
    else:
        try:
            completed = subprocess.run(
                [opencli, "twitter", "search", "football match result", "-f", "json"],
                check=True,
                capture_output=True,
                text=True,
                timeout=45,
                env=tool_environment(),
            )
            payload = json.loads(completed.stdout)
            checks["agent_reach_x"] = {
                "available": isinstance(payload, list),
                "backend": "Twitter/X via OpenCLI",
            }
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            checks["agent_reach_x"] = {
                "available": False,
                "backend": "Twitter/X via OpenCLI",
                "error": f"{type(error).__name__}: connectivity probe failed",
            }
    return checks


def preflight(config: dict[str, Any]) -> dict[str, Any]:
    required_paths = {
        "collector_root": _path(config, "collector_root"),
        "system_prompts_and_models": _path(config, "system_prompts_and_models"),
        "jaguartv_v7_pack": _path(config, "jaguartv_v7_pack"),
        "image2_database": _path(config, "image2_database"),
        "figure_1": _path(config, "figure_1"),
        "channel_icons": _path(config, "channel_icons"),
        "pending_review_uploader": _path(config, "pending_review_uploader"),
    }
    checks: dict[str, Any] = {
        name: {"available": path.exists(), "path": str(path)} for name, path in required_paths.items()
    }
    checks["agent_reach"] = {"available": executable_path("agent-reach") is not None}
    checks.update(_research_connectivity())
    checks["dreamina"] = {"available": shutil.which("dreamina") is not None}
    checks["ffmpeg"] = {"available": shutil.which("ffmpeg") is not None}
    checks["ffprobe"] = {"available": shutil.which("ffprobe") is not None}
    checks["codex"] = {
        "available": shutil.which("codex") is not None,
        "text_model": (config.get("reasoning") or {}).get("primary_model", "current-task"),
    }
    checks["image2_primary"] = {
        "available": _credential_available("APIMART_API_KEY"),
        "model": "gpt-image-2",
        "provider": "apimart",
    }
    checks["image2_fallback"] = {
        "available": (Path.home() / ".codex" / "auth.json").is_file(),
        "model": "gpt-image-2",
        "provider": "active-large-model-api",
    }
    checks["image2_route"] = {
        "available": checks["image2_primary"]["available"] or checks["image2_fallback"]["available"],
        "order": ["apimart", "active-large-model-api"],
    }
    checks["task1_store"] = {
        "available": (_path(config, "collector_root") / ".runtime/retention/fixtures.db").is_file()
    }
    result_sources = config.get("result_sources") or {}
    checks["api_football"] = {
        "available": _credential_available(str(result_sources.get("api_football_key_env") or "API_FOOTBALL_KEY")),
        "provider": "primary official post-match result source",
        "endpoint": result_sources.get("api_football_endpoint", "https://v3.football.api-sports.io/fixtures"),
    }
    missing = [name for name, check in checks.items() if not check.get("available")]
    optional = {"image2_primary", "image2_fallback"}
    if not (config.get("result_sources") or {}).get("api_football_required", False):
        optional.add("api_football")
    return {
        "checked_at": utc_now(),
        "checks": checks,
        "required_missing": [
            name
            for name in missing
            if name not in optional
        ],
        "optional_unavailable": [name for name in missing if name in optional],
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def run_phase1(config: dict[str, Any], target_date: date) -> dict[str, Any]:
    runtime = _path(config, "runtime_root")
    run_dir = FACTORY_ROOT / "runs" / target_date.strftime("%Y%m%d")
    lock_path = runtime / "locks" / "postmatch-daily.lock"
    db_path = runtime / "postmatch.sqlite3"
    with ProcessLock(lock_path):
        store = WorkflowStore(db_path)
        store.begin_run(target_date.isoformat())
        last_artifact = ""
        try:
            fixtures = load_task1_fixtures(_path(config, "image2_database"), target_date)
            for fixture in fixtures:
                store.upsert_fixture(fixture)
            fixture_path = run_dir / "phase0" / "task1-fixtures.json"
            write_json(
                fixture_path,
                {
                    "schema_version": "jaguartv-task1-link-v1",
                    "target_date": target_date.isoformat(),
                    "fixtures": [fixture.to_dict() for fixture in fixtures],
                },
            )
            last_artifact = str(fixture_path.resolve())
            completed, unfinished = collect_completed_results(
                _path(config, "collector_root"),
                str(config["source_url"]),
                fixtures,
                target_date,
                config.get("result_sources") or {},
            )
            revisions = []
            for result in completed:
                revision_id, created = store.record_result(result["task1_fixture_id"], result)
                revisions.append(
                    {
                        "task1_fixture_id": result["task1_fixture_id"],
                        "revision_id": revision_id,
                        "new_revision": created,
                    }
                )
            result_path = run_dir / "phase1" / "results.json"
            write_json(
                result_path,
                {
                    "schema_version": "jaguartv-postmatch-results-v1",
                    "target_date": target_date.isoformat(),
                    "generated_at": utc_now(),
                    "completed_count": len(completed),
                    "unfinished_count": len(unfinished),
                    "revisions": revisions,
                    "results": completed,
                    "unfinished": unfinished,
                },
            )
            last_artifact = str(result_path.resolve())
            store.finish_run(
                target_date.isoformat(),
                "PHASE1_COMPLETE",
                last_successful_artifact=last_artifact,
            )
            return {
                "ok": True,
                "target_date": target_date.isoformat(),
                "fixtures": len(fixtures),
                "completed": len(completed),
                "unfinished": len(unfinished),
                "new_revisions": sum(1 for value in revisions if value["new_revision"]),
                "result_path": last_artifact,
                "store_counts": store.counts(),
            }
        except Exception as error:
            store.finish_run(
                target_date.isoformat(),
                "FAILED",
                last_successful_artifact=last_artifact,
                error={"type": type(error).__name__, "message": str(error)[:500]},
            )
            raise
        finally:
            store.close()


def run_phase2(config: dict[str, Any], target_date: date) -> dict[str, Any]:
    run_dir = FACTORY_ROOT / "runs" / target_date.strftime("%Y%m%d")
    results_path = run_dir / "phase1" / "results.json"
    if not results_path.is_file():
        raise FileNotFoundError(f"Phase 1 result artifact is missing: {results_path}")
    research = build_phase2(results_path, run_dir / "phase2")
    assets = materialize_player_assets(run_dir / "phase2", _path(config, "image2_database"))
    return {**research, "player_assets": assets}


def run_all(config: dict[str, Any], target_date: date) -> dict[str, Any]:
    runtime = _path(config, "runtime_root")
    with ProcessLock(runtime / "locks" / "postmatch-full-workflow.lock"):
        phase1_attempts = []
        retry_minutes = [int(value) for value in config.get("retry_policy_minutes", [0])]
        previous_offset = 0
        phase1_result: dict[str, Any] | None = None
        for offset in retry_minutes:
            if offset > previous_offset:
                time.sleep((offset - previous_offset) * 60)
            phase1_result = run_phase1(config, target_date)
            phase1_attempts.append({"offset_minutes": offset, **phase1_result})
            previous_offset = offset
            if phase1_result["unfinished"] == 0:
                break
        if phase1_result is None:
            raise RuntimeError("retry policy did not contain an executable Phase 1 attempt")
        phase2_result = run_phase2(config, target_date)
        phase3_result = run_phase3(config, target_date, FACTORY_ROOT)
        phase4_result = run_phase4(config, target_date, FACTORY_ROOT)
        phase5_result = run_phase5(config, target_date, FACTORY_ROOT)
        return {
            "ok": bool(phase5_result["ok"]),
            "status": "COMPLETE" if phase5_result["ok"] else "BLOCKED",
            "target_date": target_date.isoformat(),
            "phase1_attempts": phase1_attempts,
            "phase2": phase2_result,
            "phase3": {key: phase3_result[key] for key in ("ok", "poster_count", "manifest")},
            "phase4": {key: phase4_result[key] for key in ("ok", "video_count", "build_manifest", "captions")},
            "phase5": phase5_result,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JaguarTV daily post-match workflow")
    parser.add_argument("command", choices=("preflight", "phase1", "phase2", "phase3", "phase4", "phase5", "run"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config(args.config)
    if args.command == "preflight":
        result = preflight(config)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1 if result["required_missing"] else 0)
    target_date = args.date or previous_brasilia_day()
    if args.command == "phase2":
        print(json.dumps(run_phase2(config, target_date), ensure_ascii=False, indent=2))
        return
    if args.command == "phase3":
        print(json.dumps(run_phase3(config, target_date, FACTORY_ROOT), ensure_ascii=False, indent=2))
        return
    if args.command == "phase4":
        print(json.dumps(run_phase4(config, target_date, FACTORY_ROOT), ensure_ascii=False, indent=2))
        return
    if args.command == "phase5":
        result = run_phase5(config, target_date, FACTORY_ROOT)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(2 if not result["ok"] else 0)
    if args.command == "run":
        try:
            result = run_all(config, target_date)
        except LockUnavailable as error:
            print(json.dumps({"ok": False, "status": "ALREADY_RUNNING", "error": str(error)}))
            raise SystemExit(75) from error
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(2 if not result["ok"] else 0)
    try:
        result = run_phase1(config, target_date)
    except LockUnavailable as error:
        print(json.dumps({"ok": False, "status": "ALREADY_RUNNING", "error": str(error)}))
        raise SystemExit(75) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
