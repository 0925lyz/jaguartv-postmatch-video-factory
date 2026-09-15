from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATEGORY_PATHS = {
    "operation": ("video", "operation"),
    "cta": ("video", "cta"),
    "music": ("audio", "music"),
    "voice": ("audio", "voiceover"),
}
CATEGORY_EXTENSIONS = {
    "operation": {".m4v", ".mkv", ".mov", ".mp4", ".webm"},
    "cta": {".m4v", ".mkv", ".mov", ".mp4", ".webm"},
    "music": {".aac", ".m4a", ".mp3", ".wav"},
    "voice": {".wav"},
}


def discover_inventory(asset_root: Path) -> dict[str, list[Path]]:
    inventory: dict[str, list[Path]] = {}
    for category, parts in CATEGORY_PATHS.items():
        directory = asset_root.joinpath(*parts)
        paths = [
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.casefold() in CATEGORY_EXTENSIONS[category]
        ]
        paths.sort(key=lambda path: (path.name.casefold(), str(path).casefold()))
        names = [path.name for path in paths]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate {category} asset filenames are not supported")
        if not paths:
            raise FileNotFoundError(f"{category} inventory is empty: {directory}")
        inventory[category] = paths
    return inventory


def inventory_fingerprint(inventory: dict[str, list[Path]]) -> str:
    digest = hashlib.sha256()
    for category in CATEGORY_PATHS:
        for path in inventory[category]:
            digest.update(f"{category}\0{path.name}\0{path.stat().st_size}\n".encode())
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": "jaguartv-media-rotation-v1", "categories": {}, "pending": {}, "completed": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid media rotation state: {path}")
    payload.setdefault("schema_version", "jaguartv-media-rotation-v1")
    payload.setdefault("categories", {})
    payload.setdefault("pending", {})
    payload.setdefault("completed", {})
    return payload


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _reconcile(category_state: dict[str, Any], names: list[str]) -> dict[str, Any]:
    used = [name for name in category_state.get("used", []) if name in names]
    remaining = [
        name for name in category_state.get("remaining", [])
        if name in names and name not in used
    ]
    known = set(used) | set(remaining)
    remaining.extend(name for name in names if name not in known)
    cycle = max(1, int(category_state.get("cycle", 1)))
    if not remaining:
        remaining = list(names)
        used = []
        cycle += 1
    return {"cycle": cycle, "used": used, "remaining": remaining}


def _take(category_state: dict[str, Any], names: list[str], count: int) -> tuple[list[str], list[dict[str, Any]]]:
    selected: list[str] = []
    positions: list[dict[str, Any]] = []
    state = _reconcile(category_state, names)
    for _ in range(count):
        if not state["remaining"]:
            state = {"cycle": state["cycle"] + 1, "used": [], "remaining": list(names)}
        name = state["remaining"].pop(0)
        state["used"].append(name)
        selected.append(name)
        positions.append({
            "filename": name,
            "cycle": state["cycle"],
            "position": len(state["used"]),
            "inventory_size": len(names),
        })
    category_state.clear()
    category_state.update(state)
    return selected, positions


def _materialize(record: dict[str, Any], inventory: dict[str, list[Path]]) -> dict[str, Any]:
    lookup = {category: {path.name: str(path) for path in paths} for category, paths in inventory.items()}
    selected = record["selected"]
    try:
        components = {
            "operation": [lookup["operation"][name] for name in selected["operation"]],
            "cta": lookup["cta"][selected["cta"][0]],
            "music": lookup["music"][selected["music"][0]],
            "voice": lookup["voice"][selected["voice"][0]],
        }
    except KeyError as error:
        raise FileNotFoundError(f"Reserved media asset is no longer in inventory: {error.args[0]}") from error
    return {
        "usage_id": record["usage_id"],
        "inventory_fingerprint": record["inventory_fingerprint"],
        "components": components,
        "positions": record["positions"],
        "committed": bool(record.get("committed_at")),
    }


def reserve_rotation(
    state_path: Path,
    inventory: dict[str, list[Path]],
    usage_id: str,
    *,
    operation_count: int = 2,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    if operation_count < 1:
        raise ValueError("operation_count must be positive")
    state = _read_state(state_path)
    for collection in (state["completed"], state["pending"]):
        if usage_id in collection:
            return _materialize(collection[usage_id], inventory)

    projected = deepcopy(state["categories"])
    if state["pending"]:
        projected = deepcopy(next(reversed(state["pending"].values()))["after_categories"])
    counts = {"operation": operation_count, "cta": 1, "music": 1, "voice": 1}
    selected: dict[str, list[str]] = {}
    positions: dict[str, list[dict[str, Any]]] = {}
    for category, count in counts.items():
        names = [path.name for path in inventory[category]]
        category_state = projected.setdefault(category, {})
        selected[category], positions[category] = _take(category_state, names, count)
    record = {
        "usage_id": usage_id,
        "inventory_fingerprint": fingerprint or inventory_fingerprint(inventory),
        "selected": selected,
        "positions": positions,
        "after_categories": projected,
        "reserved_at": _now(),
    }
    state["pending"][usage_id] = record
    _write_state(state_path, state)
    return _materialize(record, inventory)


def commit_rotation(state_path: Path, usage_id: str) -> dict[str, Any]:
    state = _read_state(state_path)
    if usage_id in state["completed"]:
        return state["completed"][usage_id]
    pending = state["pending"]
    if usage_id not in pending:
        raise KeyError(f"No pending media reservation for {usage_id}")
    if next(iter(pending)) != usage_id:
        raise RuntimeError(f"Media reservations must commit in order; next is {next(iter(pending))}")
    record = pending.pop(usage_id)
    state["categories"] = record.pop("after_categories")
    record["committed_at"] = _now()
    state["completed"][usage_id] = record
    while len(state["completed"]) > 500:
        del state["completed"][next(iter(state["completed"]))]
    _write_state(state_path, state)
    return record


def discard_stale_pending(state_path: Path, valid_usage_ids: set[str]) -> None:
    state = _read_state(state_path)
    retained: dict[str, Any] = {}
    for usage_id, record in state["pending"].items():
        if usage_id not in valid_usage_ids:
            break
        retained[usage_id] = record
    if retained != state["pending"]:
        state["pending"] = retained
        _write_state(state_path, state)
