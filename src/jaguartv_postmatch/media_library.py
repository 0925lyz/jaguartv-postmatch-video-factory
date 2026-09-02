from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
FINISHED_MARKERS = ("final-", "成片-", "final-video", "jaguartv")
FINISHED_EXCLUSIONS = (
    "hook", "动态钩子", "dreamina", "即梦", "raw", "visual_clean", "visual-clean", "cta",
)
OPERATION_MARKERS = ("operation", "downloader", "google-search", "football-epg", "操作")
PREMATCH_POSTER_EXCLUSIONS = (
    "cover", "master-", "母版-", "background", "reference", "raw", "preview",
    "discard", "rejected", "/qa/", "contact", "hook", "cta", "resultados",
    "postmatch", "赛后",
)


@dataclass(frozen=True)
class LibraryArtifact:
    category: str
    source: str
    destination: str
    source_sha256: str
    output_sha256: str
    operation: str
    duration_seconds: float | None = None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iter_files(roots: Iterable[Path], extensions: set[str]) -> Iterable[Path]:
    for root in roots:
        resolved = root.expanduser().resolve()
        if not resolved.exists():
            continue
        if resolved.is_file() and resolved.suffix.casefold() in extensions:
            yield resolved
            continue
        for path in resolved.rglob("*"):
            if path.is_file() and path.suffix.casefold() in extensions:
                yield path.resolve()


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    completed = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(completed.stdout.strip())


def discover_finished_videos(
    roots: Iterable[Path], ffprobe: str = "ffprobe",
) -> list[tuple[Path, float]]:
    found: list[tuple[Path, float]] = []
    for path in iter_files(roots, VIDEO_EXTENSIONS):
        lowered = str(path).casefold()
        name = path.name.casefold()
        if any(token in lowered for token in FINISHED_EXCLUSIONS):
            continue
        if not any(marker in name for marker in FINISHED_MARKERS):
            continue
        try:
            duration = probe_duration(path, ffprobe)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if duration >= 9.0:
            found.append((path, duration))
    return sorted(found, key=lambda item: str(item[0]))


def discover_postmatch_posters(roots: Iterable[Path]) -> list[Path]:
    excluded = (
        "/raw/", "/rejected/", "/_discard/", "/qa/", "contact_sheet",
        "preview", "master-", "母版-",
    )
    return sorted(
        (
            path
            for path in iter_files(roots, IMAGE_EXTENSIONS)
            if not any(token in str(path).casefold() for token in excluded)
        ),
        key=str,
    )


def discover_prematch_posters(roots: Iterable[Path]) -> list[Path]:
    results: list[Path] = []
    for path in iter_files(roots, IMAGE_EXTENSIONS):
        lowered = str(path).casefold()
        name = path.name.casefold()
        if any(token in lowered for token in PREMATCH_POSTER_EXCLUSIONS):
            continue
        if "海报" in path.name or "poster" in name or "schedule" in name or "agenda" in name:
            results.append(path)
    return sorted(results, key=str)


def discover_operation_clips(roots: Iterable[Path]) -> list[Path]:
    return sorted(
        (
            path
            for path in iter_files(roots, VIDEO_EXTENSIONS)
            if any(marker in path.name.casefold() for marker in OPERATION_MARKERS)
            and "cta" not in str(path).casefold()
        ),
        key=str,
    )


def extract_segment(
    source: Path,
    target: Path,
    *,
    start: float,
    duration: float,
    ffmpeg: str = "ffmpeg",
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
            "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", str(target),
        ],
        check=True,
    )


def _copy_unique(
    source: Path,
    target: Path,
    category: str,
    artifacts: list[LibraryArtifact],
) -> None:
    source_hash = sha256(source)
    if not target.exists() or sha256(target) != source_hash:
        shutil.copy2(source, target)
    artifacts.append(
        LibraryArtifact(category, str(source), str(target), source_hash, source_hash, "copy")
    )


def _collision_safe_target(directory: Path, filename: str, digest: str) -> Path:
    target = directory / filename
    if not target.exists() or sha256(target) == digest:
        return target
    return directory / f"{target.stem}_{digest[:10]}{target.suffix.casefold()}"


def _deduplicate(paths: Iterable[Path]) -> list[tuple[Path, str]]:
    seen: set[str] = set()
    unique: list[tuple[Path, str]] = []
    for path in paths:
        digest = sha256(path)
        if digest in seen:
            continue
        seen.add(digest)
        unique.append((path, digest))
    return unique


def _valid_segment(path: Path, expected_duration: float, ffprobe: str) -> bool:
    if not path.is_file() or path.stat().st_size < 10_000:
        return False
    try:
        duration = probe_duration(path, ffprobe)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return abs(duration - expected_duration) <= 0.15


def build_media_library(
    destination: Path,
    *,
    finished_video_roots: list[Path],
    prematch_poster_roots: list[Path],
    postmatch_poster_roots: list[Path],
    operation_roots: list[Path],
    cta_roots: list[Path],
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> dict[str, object]:
    destination = destination.expanduser().resolve()
    final_dir = destination / "成品海报视频"
    prematch_poster_dir = destination / "赛前海报"
    poster_dir = destination / "赛后海报"
    operation_dir = destination / "操作类"
    cta_dir = destination / "cta"
    for directory in (final_dir, prematch_poster_dir, poster_dir, operation_dir, cta_dir):
        directory.mkdir(parents=True, exist_ok=True)

    artifacts: list[LibraryArtifact] = []
    finished_by_hash: list[tuple[Path, float, str]] = []
    seen_videos: set[str] = set()
    for source, duration in discover_finished_videos(finished_video_roots, ffprobe):
        digest = sha256(source)
        if digest in seen_videos:
            continue
        seen_videos.add(digest)
        finished_by_hash.append((source, duration, digest))

    for index, (source, duration, digest) in enumerate(finished_by_hash, start=1):
        target = final_dir / f"成品海报视频_{index:03d}{source.suffix.casefold()}"
        _copy_unique(source, target, "成品海报视频", artifacts)
        operation_target = operation_dir / f"操作类_{index:03d}_4至9秒.mp4"
        if not _valid_segment(operation_target, 5.0, ffprobe):
            extract_segment(source, operation_target, start=4.0, duration=5.0, ffmpeg=ffmpeg)
        artifacts.append(
            LibraryArtifact(
                "操作类", str(source), str(operation_target), digest,
                sha256(operation_target), "extract_4_to_9_seconds",
                probe_duration(operation_target, ffprobe),
            )
        )
        cta_target = cta_dir / f"CTA片段_{index:03d}_末3秒.mp4"
        if not _valid_segment(cta_target, 3.0, ffprobe):
            extract_segment(
                source, cta_target, start=max(0.0, duration - 3.0), duration=3.0, ffmpeg=ffmpeg,
            )
        artifacts.append(
            LibraryArtifact(
                "cta", str(source), str(cta_target), digest,
                sha256(cta_target), "extract_final_3_seconds",
                probe_duration(cta_target, ffprobe),
            )
        )

    prematch_posters = _deduplicate(discover_prematch_posters(prematch_poster_roots))
    for source, digest in prematch_posters:
        target = _collision_safe_target(prematch_poster_dir, source.name, digest)
        _copy_unique(source, target, "赛前海报", artifacts)

    posters = _deduplicate(discover_postmatch_posters(postmatch_poster_roots))
    for index, (source, _digest) in enumerate(posters, start=1):
        suffix = source.suffix.casefold()
        name = source.name if re.search(r"[\u4e00-\u9fff]", source.name) else f"赛后海报_{index:03d}{suffix}"
        _copy_unique(source, poster_dir / name, "赛后海报", artifacts)

    operations = _deduplicate(discover_operation_clips(operation_roots))
    for index, (source, _digest) in enumerate(operations, start=1):
        target = operation_dir / f"操作类库存_{index:03d}{source.suffix.casefold()}"
        _copy_unique(source, target, "操作类", artifacts)

    cta_assets = _deduplicate(iter_files(cta_roots, VIDEO_EXTENSIONS | IMAGE_EXTENSIONS))
    for index, (source, _digest) in enumerate(cta_assets, start=1):
        target = cta_dir / f"CTA库存_{index:03d}{source.suffix.casefold()}"
        _copy_unique(source, target, "cta", artifacts)

    counts = {
        category: sum(item.category == category for item in artifacts)
        for category in ("成品海报视频", "赛前海报", "赛后海报", "操作类", "cta")
    }
    manifest = {
        "schema_version": "jaguartv-media-library-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "destination": str(destination),
        "rules": {
            "deduplication": "SHA-256 per source category",
            "finished_video": "named final/成片/JaguarTV videos at least 9 seconds",
            "operation_extract": "seconds 4.000 through 9.000 from each unique finished video",
            "cta_extract": "final 3.000 seconds from each unique finished video",
            "source_files_mutated": False,
        },
        "counts": counts,
        "artifacts": [asdict(item) for item in artifacts],
    }
    (destination / "交付清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    (destination / "README.md").write_text(
        "# 赛前海报视频交付\n\n"
        "- `成品海报视频`：当前项目中发现并按内容去重的已保存成片。\n"
        "- `赛后海报`：已完成赛后海报，不含 raw、rejected、QA 或废弃版本。\n"
        "- `操作类`：V7 操作库存，以及每个成片的 4-9 秒操作段。\n"
        "- `cta`：现有 CTA 库存，以及每个成片的末 3 秒 CTA。\n"
        "- `交付清单.json`：原始路径、目标路径、时长和 SHA-256。\n",
        encoding="utf-8",
    )
    return manifest


def load_config(path: Path) -> dict[str, object]:
    config_path = path.expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("media library config must be an object")
    base = config_path.parent

    def paths(key: str) -> list[Path]:
        values = payload.get(key) or []
        if not isinstance(values, list):
            raise ValueError(f"{key} must be a list")
        return [Path(str(value)).expanduser() for value in values]

    destination = Path(str(payload["destination"])).expanduser()
    if not destination.is_absolute():
        destination = (base.parent / destination).resolve()
    return {
        "destination": destination,
        "finished_video_roots": paths("finished_video_roots"),
        "prematch_poster_roots": paths("prematch_poster_roots"),
        "postmatch_poster_roots": paths("postmatch_poster_roots"),
        "operation_roots": paths("operation_roots"),
        "cta_roots": paths("cta_roots"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the JaguarTV media delivery library")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    manifest = build_media_library(**load_config(args.config))
    print(json.dumps(manifest["counts"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
