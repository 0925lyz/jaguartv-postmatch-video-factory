from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from jaguartv_factory.core import connect_db, load_config, now_iso
from jaguartv_factory.original_factory import _audit, original_storage_root
from jaguartv_factory.server_store import public_url, storage_root


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or value.startswith("/") or ".." in path.parts:
        raise ValueError("unsafe managed relative path")
    return path


def matching_items(connection, workflow_identity: str) -> list[dict]:
    matches = []
    for row in connection.execute(
        "SELECT * FROM original_factory_items WHERE deleted_at IS NULL ORDER BY created_at"
    ):
        metadata = json.loads(row["metadata_json"] or "{}")
        if metadata.get("workflow_identity") == workflow_identity:
            item = dict(row)
            item["metadata"] = metadata
            matches.append(item)
    return matches


def inspect(workflow_identity: str) -> dict:
    config = load_config()
    connection = connect_db(config)
    matches = matching_items(connection, workflow_identity)
    root = storage_root(config)
    original_root = original_storage_root(config)
    items = []
    for item in matches:
        metadata = item["metadata"]
        associations = metadata.get("associations") or []
        attachments_verified = bool(associations)
        for association in associations:
            relative = safe_relative(str(association.get("server_relative_path") or ""))
            path = root.joinpath(*relative.parts)
            if not path.is_file() or sha256(path) != association.get("sha256"):
                attachments_verified = False
                break
        thumbnail = original_root.joinpath(*safe_relative(item["thumbnail_key"]).parts)
        exact_cover_verified = bool(
            thumbnail.is_file()
            and metadata.get("cover_sha256")
            and sha256(thumbnail) == metadata.get("cover_sha256")
        )
        items.append(
            {
                "id": item["id"],
                "status": item["status"],
                "category": item["category"],
                "sha256": item["sha256"],
                "artifact_revision": metadata.get("artifact_revision"),
                "association_count": len(associations),
                "attachments_verified": attachments_verified,
                "exact_cover_verified": exact_cover_verified,
            }
        )
    return {
        "count": len(matches),
        "items": items,
    }


def probe_video(path: Path) -> dict:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,codec_name:format=duration",
            "-of", "json", str(path),
        ],
        text=True,
        capture_output=True,
        timeout=60,
        check=True,
    )
    payload = json.loads(completed.stdout)
    stream = payload["streams"][0]
    return {
        "duration_sec": float(payload["format"]["duration"]),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "video_codec": str(stream.get("codec_name") or ""),
    }


def finalize(package_dir: Path, item_id: str, workflow_identity: str) -> dict:
    incoming_root = Path("/tmp/jaguartv-postmatch-incoming").resolve()
    package_dir = package_dir.resolve()
    if incoming_root not in package_dir.parents:
        raise ValueError("package must be inside the controlled incoming root")
    metadata_path = package_dir / "upload-metadata.json"
    package_manifest_path = package_dir / "package-manifest.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    package_manifest = json.loads(package_manifest_path.read_text(encoding="utf-8"))
    if metadata.get("metadata", {}).get("workflow_identity") != workflow_identity:
        raise ValueError("workflow identity does not match package metadata")
    for artifact in package_manifest["files"]:
        path = package_dir / artifact["package_name"]
        if not path.is_file() or sha256(path) != artifact["sha256"]:
            raise ValueError(f"package hash validation failed for {artifact['package_name']}")

    config = load_config()
    connection = connect_db(config)
    matches = matching_items(connection, workflow_identity)
    if len(matches) > 1:
        raise RuntimeError("duplicate active workflow identities require operator review")
    if matches and matches[0]["id"] != item_id:
        raise RuntimeError("uploaded item conflicts with an existing workflow identity")
    row = connection.execute(
        "SELECT * FROM original_factory_items WHERE id=? AND deleted_at IS NULL", (item_id,)
    ).fetchone()
    if row is None:
        raise RuntimeError("uploaded original item was not found")
    if row["status"] != "PENDING_REVIEW":
        raise RuntimeError("only a pending-review original may be updated")

    relative_package = safe_relative(package_manifest["server_relative_dir"])
    destination = storage_root(config).joinpath(*relative_package.parts).resolve()
    review_root = (storage_root(config) / "review").resolve()
    if review_root not in destination.parents:
        raise ValueError("package destination must remain inside managed review storage")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_destination = Path(tempfile.mkdtemp(prefix=".postmatch-", dir=destination.parent))
    for artifact in package_manifest["files"]:
        shutil.copy2(package_dir / artifact["package_name"], temporary_destination / artifact["package_name"])
    if destination.exists():
        shutil.rmtree(destination)
    os.replace(temporary_destination, destination)

    video_source = package_dir / "final-video.mp4"
    expected_video_sha = metadata["metadata"]["video_sha256"]
    current_video_sha = str(row["sha256"])
    video_replaced = False
    if current_video_sha != expected_video_sha:
        if not video_source.is_file() or sha256(video_source) != expected_video_sha:
            raise ValueError("corrected video is missing or failed hash validation")
        probe = probe_video(video_source)
        video_path = original_storage_root(config).joinpath(*safe_relative(row["file_key"]).parts)
        replacement = video_path.with_suffix(".replacement.mp4")
        shutil.copy2(video_source, replacement)
        os.replace(replacement, video_path)
        connection.execute(
            """UPDATE original_factory_items
               SET size_bytes=?,sha256=?,duration_sec=?,width=?,height=?,video_codec=?,updated_at=?
               WHERE id=?""",
            (
                video_path.stat().st_size, expected_video_sha, probe["duration_sec"],
                probe["width"], probe["height"], probe["video_codec"], now_iso(), item_id,
            ),
        )
        video_replaced = True

    cover = destination / "cover.jpg"
    expected_cover_sha = metadata["metadata"]["cover_sha256"]
    if sha256(cover) != expected_cover_sha:
        raise ValueError("exact cover failed final hash validation")
    thumbnail = original_storage_root(config).joinpath(*safe_relative(row["thumbnail_key"]).parts)
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    thumbnail_temporary = thumbnail.with_suffix(".replacement.jpg")
    shutil.copy2(cover, thumbnail_temporary)
    os.replace(thumbnail_temporary, thumbnail)

    metadata["metadata"]["attachment_contract_required"] = False
    metadata["metadata"]["source_poster_and_cover_uploaded"] = True
    metadata["metadata"]["package_public_url"] = public_url(config, relative_package)
    metadata["metadata"]["server_verified_at"] = now_iso()
    timestamp = now_iso()
    match_time = metadata.get("match_time_brasilia") or metadata.get("match_time_sao_paulo")
    connection.execute(
        """UPDATE original_factory_items
           SET name=?,match_name=?,match_date=?,match_time_sao_paulo=?,channels_json=?,
               generated_at=?,match_info_json=?,metadata_json=?,updated_at=?
           WHERE id=?""",
        (
            metadata["match_name"], metadata["match_name"], metadata["match_date"],
            match_time, json.dumps(metadata["channels"], ensure_ascii=False),
            metadata["generated_at"], json.dumps(metadata["match_info"], ensure_ascii=False),
            json.dumps(metadata["metadata"], ensure_ascii=False), timestamp, item_id,
        ),
    )
    connection.execute("DELETE FROM original_factory_social_sources WHERE item_id=?", (item_id,))
    for source in metadata.get("social_sources", []):
        connection.execute(
            """INSERT INTO original_factory_social_sources(
                 id,item_id,source_url,platform,fetched_at,summary,confidence,uncertain,
                 image_source_url,image_license_status,metadata_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex, item_id, source["source_url"], source["platform"],
                source["fetched_at"], source["summary"], source["confidence"],
                int(source["uncertain"]), source["image_source_url"],
                source["image_license_status"], json.dumps(source["metadata"], ensure_ascii=False),
                timestamp,
            ),
        )
    audit_request_id = f"postmatch-package-{metadata['metadata']['artifact_revision'][:24]}"
    if not connection.execute(
        "SELECT 1 FROM original_factory_audit_events WHERE request_id=?", (audit_request_id,)
    ).fetchone():
        _audit(
            connection,
            item_id=item_id,
            action="ATTACH_PACKAGE",
            from_status="PENDING_REVIEW",
            to_status="PENDING_REVIEW",
            actor="postmatch-worker",
            request_id=audit_request_id,
            metadata={
                "workflow_identity": workflow_identity,
                "artifact_revision": metadata["metadata"]["artifact_revision"],
                "association_count": len(metadata["metadata"]["associations"]),
                "exact_cover_verified": True,
                "video_replaced": video_replaced,
            },
            created_at=timestamp,
        )
    connection.commit()
    shutil.rmtree(package_dir)
    return {
        "id": item_id,
        "status": "PENDING_REVIEW",
        "category": str(row["category"]),
        "association_count": len(metadata["metadata"]["associations"]),
        "exact_cover_verified": sha256(thumbnail) == expected_cover_sha,
        "video_sha256": expected_video_sha,
        "video_replaced": video_replaced,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inspect", "finalize"))
    parser.add_argument("--workflow-identity", required=True)
    parser.add_argument("--package-dir", type=Path)
    parser.add_argument("--item-id", default="")
    args = parser.parse_args()
    if args.mode == "inspect":
        result = inspect(args.workflow_identity)
    else:
        if not args.package_dir or not args.item_id:
            raise ValueError("finalize requires package-dir and item-id")
        result = finalize(args.package_dir, args.item_id, args.workflow_identity)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
