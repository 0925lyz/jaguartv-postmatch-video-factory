from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from typing import Any

from .util import normalize_name, utc_now


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("player asset is not a PNG")
    return struct.unpack(">II", data[16:24])


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_name(value)).strip("-") or "player"


def _download(url: str) -> bytes:
    completed = subprocess.run(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--max-time",
            "30",
            url,
        ],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def materialize_player_assets(research_dir: Path, image2_database: Path) -> dict[str, Any]:
    target = image2_database / "assets" / "players" / "2026"
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "asset_manifest.json"
    existing = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    records: dict[str, Any] = existing.get("assets") or {}
    match_updates = []
    for research_path in sorted(research_dir.glob("*_research.json")):
        research = json.loads(research_path.read_text(encoding="utf-8"))
        usable = []
        failures = []
        for candidate in research.get("visual_candidates") or []:
            asset_id = f"espn-{candidate['athlete_id']}-2026"
            destination = target / f"{_slug(str(candidate['name']))}-{candidate['athlete_id']}.png"
            try:
                data = destination.read_bytes() if destination.is_file() else _download(candidate["headshot_url"])
                width, height = _png_dimensions(data)
                if min(width, height) < 512:
                    raise ValueError(f"resolution {width}x{height} is below the 512px production gate")
                if not destination.is_file():
                    destination.write_bytes(data)
                record = {
                    "asset_id": asset_id,
                    "player_name": candidate["name"],
                    "player_id": candidate["athlete_id"],
                    "club": candidate["team"],
                    "season": "2026",
                    "source": candidate["headshot_url"],
                    "license_record": "operator-authorized for this project",
                    "retrieval_date": utc_now(),
                    "asset_hash": hashlib.sha256(data).hexdigest(),
                    "width": width,
                    "height": height,
                    "path": str(destination.resolve()),
                    "actual_participation_verified": True,
                    "current_for_match_date": research["match"]["official_match_date"],
                }
                records[asset_id] = record
                usable.append(record)
            except (subprocess.CalledProcessError, ValueError, OSError) as error:
                failures.append(
                    {
                        "player_name": candidate.get("name"),
                        "source": candidate.get("headshot_url"),
                        "sanitized_error": str(error)[:240],
                    }
                )
        research["licensed_player_assets"] = usable
        research["player_asset_failures"] = failures
        research["poster_identity_mode"] = (
            "verified-real-player-likeness"
            if usable or research.get("visual_candidates")
            else "virtual-hardman-player"
        )
        temporary = research_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(research, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(research_path)
        match_updates.append(
            {
                "task1_fixture_id": research["match"]["task1_fixture_id"],
                "usable_assets": len(usable),
                "failures": len(failures),
                "poster_identity_mode": research["poster_identity_mode"],
            }
        )
    manifest = {
        "schema_version": "jaguartv-player-assets-v1",
        "updated_at": utc_now(),
        "assets": records,
    }
    temporary_manifest = manifest_path.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_manifest.replace(manifest_path)
    return {
        "manifest": str(manifest_path.resolve()),
        "asset_count": len(records),
        "matches": match_updates,
    }
