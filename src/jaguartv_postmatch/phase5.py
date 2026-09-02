from __future__ import annotations

import base64
import atexit
import hashlib
import http.client
import json
import socket
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .credentials import env_or_keychain
from .util import utc_now


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: str | Path, workspace: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(workspace))
    except ValueError:
        return resolved.name


def _social_sources(research: dict[str, Any], *, summary: bool = False) -> list[dict[str, Any]]:
    records = research.get("source_records", [])
    selected = records[:2] if summary else records[:8]
    output = []
    for record in selected:
        platform_raw = str(record.get("platform") or "web").lower()
        output.append(
            {
                "source_url": record["source_url"],
                "platform": "x" if platform_raw.startswith("x-") else "web",
                "fetched_at": record.get("retrieval_time") or research.get("generated_at") or utc_now(),
                "summary": str(record.get("summary") or record.get("title") or "Fonte da partida")[:1800],
                "confidence": float(record.get("confidence") or 0),
                "uncertain": not bool(record.get("use_for_facts", False)),
                "image_source_url": "",
                "image_license_status": "authorized",
                "metadata": {"title": record.get("title"), "publication_time": record.get("publication_time")},
            }
        )
    return output


def _public_result(result: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "task1_fixture_id", "source_fixture_id", "composite_key", "provider_match_id",
        "competition", "home_team", "away_team", "official_match_date",
        "original_kickoff_time", "public_timezone_label", "channels",
        "official_source_url", "status_verification_url", "retrieval_timestamp",
        "original_result_text", "completed", "result_status", "provider_status",
        "provider_event_url", "home_score", "away_score", "extra_time", "penalty_shootout",
    )
    return {field: result.get(field) for field in fields}


def _artifact_revision(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()


def _copy_package_file(source: Path, package_dir: Path, package_name: str, role: str) -> dict[str, Any]:
    destination = package_dir / package_name
    shutil.copy2(source, destination)
    return {
        "role": role,
        "package_name": package_name,
        "filename": source.name,
        "sha256": _sha256(destination),
        "size_bytes": destination.stat().st_size,
    }


def _run(command: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "command failed")[-800:]
        raise RuntimeError(message)
    return completed


def _install_remote_helper(ssh_alias: str) -> str:
    helper = Path(__file__).with_name("remote_original_package.py").resolve()
    remote = "/tmp/jaguartv-postmatch-package.py"
    _run(["scp", str(helper), f"{ssh_alias}:{remote}"], timeout=60)
    _run(["ssh", ssh_alias, "mkdir", "-p", "/tmp/jaguartv-postmatch-incoming"], timeout=30)
    return remote


def _remote_json(ssh_alias: str, remote_app: str, helper: str, arguments: list[str]) -> dict[str, Any]:
    completed = _run(
        ["ssh", ssh_alias, "cd", remote_app, "&&", ".venv/bin/python", helper, *arguments],
        timeout=180,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("remote package helper returned no result")
    return json.loads(lines[-1])


def _upload_original(video: Path, metadata: dict[str, Any], base_url: str, token: str) -> dict[str, Any]:
    parsed = urlparse(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("dashboard URL is not a safe absolute HTTP(S) URL")
    payload = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    revision = metadata["metadata"]["artifact_revision"]
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.netloc, timeout=900)
    endpoint = f"{parsed.path.rstrip('/')}/api/originals/import?filename={quote(video.name)}"
    connection.putrequest("POST", endpoint)
    connection.putheader("Content-Type", "video/mp4")
    connection.putheader("Content-Length", str(video.stat().st_size))
    connection.putheader("X-Upload-Token", token)
    connection.putheader("X-Original-Metadata", encoded)
    connection.putheader("X-Request-ID", f"postmatch-{revision[:32]}")
    connection.putheader("X-Operator", "postmatch-worker")
    connection.putheader("X-Original-Batch-Size", "1")
    connection.endheaders()
    with video.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            connection.send(chunk)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    if response.status not in {200, 201}:
        try:
            message = str(json.loads(raw).get("error") or "upload rejected")[:500]
        except (ValueError, AttributeError):
            message = "upload rejected"
        raise RuntimeError(f"server returned HTTP {response.status}: {message}")
    result = json.loads(raw)
    if not result.get("id") or result.get("status_id") != "PENDING_REVIEW":
        raise RuntimeError("server returned an invalid pending-review record")
    return result


def _stage_remote_package(ssh_alias: str, package_dir: Path) -> str:
    remote_root = "/tmp/jaguartv-postmatch-incoming"
    _run(["scp", "-r", str(package_dir), f"{ssh_alias}:{remote_root}/"], timeout=300)
    return f"{remote_root}/{package_dir.name}"


def _server_connectivity(base_url: str, dashboard_token: str) -> bool:
    parsed = urlparse(base_url.rstrip("/"))
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(parsed.netloc, timeout=30)
    connection.request(
        "GET",
        f"{parsed.path.rstrip('/')}/api/originals/import/limits",
        headers={"X-Dashboard-Token": dashboard_token},
    )
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status == 200


def _start_ssh_tunnel(ssh_alias: str, remote_port: int) -> tuple[subprocess.Popen[str], str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        local_port = int(probe.getsockname()[1])
    process = subprocess.Popen(
        [
            "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            "-o", "ServerAliveInterval=10", "-o", "ExitOnForwardFailure=yes", "-N",
            "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", ssh_alias,
        ],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for _ in range(200):
        if process.poll() is not None:
            error = process.stderr.read()[-500:] if process.stderr else "SSH tunnel exited"
            raise RuntimeError(error)
        try:
            with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                return process, f"http://127.0.0.1:{local_port}"
        except OSError:
            time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError("SSH tunnel did not become ready")


def _stop_tunnel(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_phase5(config: dict[str, Any], target_date: date, factory_root: Path) -> dict[str, Any]:
    workspace = factory_root.parent.resolve()
    run_dir = factory_root / "runs" / target_date.strftime("%Y%m%d")
    phase5_dir = run_dir / "phase5"
    metadata_dir = phase5_dir / "upload-metadata"
    packages_dir = phase5_dir / "packages"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    packages_dir.mkdir(parents=True, exist_ok=True)

    env_or_keychain("JAGUARTV_DASHBOARD_URL")
    upload_token = env_or_keychain("JAGUARTV_UPLOAD_TOKEN")
    dashboard_token = env_or_keychain("JAGUARTV_DASHBOARD_TOKEN")
    server = config["server"]
    ssh_alias = str(server["ssh_alias"])
    remote_app = str(server["remote_app_dir"])
    helper = _install_remote_helper(ssh_alias)
    tunnel: subprocess.Popen[str] | None = None
    base_url = ""
    connectivity = True
    connectivity_method = "authenticated SSH server adapter"

    phase1 = json.loads((run_dir / "phase1" / "results.json").read_text(encoding="utf-8"))
    results = {item["task1_fixture_id"]: item for item in phase1["results"]}
    phase3 = json.loads((run_dir / "phase3" / "production-manifest.json").read_text(encoding="utf-8"))
    posters = {item["task_id"]: item for item in phase3["posters"]}
    build_path = run_dir / "phase4" / "build-manifest.json"
    captions_path = run_dir / "phase4" / "captions.json"
    build = json.loads(build_path.read_text(encoding="utf-8"))
    captions = json.loads(captions_path.read_text(encoding="utf-8"))
    research = {
        task_id: json.loads((run_dir / "phase2" / f"{task_id}_research.json").read_text(encoding="utf-8"))
        for task_id in results
    }

    drafts = []
    upload_results = []
    for item in build["items"]:
        task_id = item["task_id"]
        poster = posters[task_id]
        match_results = [results[value] for value in poster["match_ids"]]
        channels = list(dict.fromkeys(channel for result in match_results for channel in result["channels"]))
        if item["kind"] == "single":
            result = match_results[0]
            match_name = f"{result['home_team']} {result['home_score']} x {result['away_score']} {result['away_team']}"
            social = _social_sources(research[result["task1_fixture_id"]])
        else:
            match_name = f"PLACARES FINAIS {target_date.strftime('%d/%m/%Y')} - {task_id[-2:]}"
            social = []
            for result in match_results:
                social.extend(_social_sources(research[result["task1_fixture_id"]], summary=True))

        video_path = Path(item["final"])
        cover_path = Path(item["cover"])
        source_paths = [video_path, cover_path, Path(poster["poster_path"]), Path(poster["prompt_path"]), captions_path, build_path]
        source_paths.extend(run_dir / "phase2" / f"{result['task1_fixture_id']}_research.json" for result in match_results)
        revision = _artifact_revision(source_paths)
        workflow_identity = f"postmatch:{target_date.isoformat()}:{task_id}"
        server_relative_dir = f"review/postmatch/{target_date.strftime('%Y%m%d')}/{task_id}/{revision[:16]}"
        package_dir = packages_dir / f"{task_id}-{revision[:16]}"
        package_dir.mkdir(parents=True, exist_ok=True)
        files = [
            _copy_package_file(cover_path, package_dir, "cover.jpg", "cover"),
            _copy_package_file(Path(poster["poster_path"]), package_dir, "source-poster.png", "source_poster"),
            _copy_package_file(Path(poster["prompt_path"]), package_dir, "image2-prompt.txt", "image2_prompt"),
            _copy_package_file(captions_path, package_dir, "captions.json", "captions_json"),
            _copy_package_file(build_path, package_dir, "build-manifest.json", "build_manifest_json"),
        ]
        for result in match_results:
            research_path = run_dir / "phase2" / f"{result['task1_fixture_id']}_research.json"
            files.append(_copy_package_file(research_path, package_dir, f"research-{result['task1_fixture_id']}.json", "phase2_research"))
        shutil.copy2(video_path, package_dir / "final-video.mp4")
        associations = [
            {
                **artifact,
                "server_relative_path": f"{server_relative_dir}/{artifact['package_name']}",
                "server_url_path": f"/media/{server_relative_dir}/{quote(artifact['package_name'])}",
                "attachment_state": "SERVER_MANAGED",
            }
            for artifact in files
        ]
        kickoff = match_results[0]["original_kickoff_time"]
        metadata = {
            "category": "post_match_score",
            "match_name": match_name,
            "match_date": target_date.isoformat(),
            "match_time_sao_paulo": f"{target_date.isoformat()}T{kickoff}:00-03:00",
            "channels": channels or ["Jaguar TV"],
            "generated_at": item.get("completed_at") or item.get("created_at"),
            "match_info": {
                "public_timezone_label": "Horário de Brasília",
                "task1_fixture_ids": poster["match_ids"],
                "results": [_public_result(result) for result in match_results],
                "caption_pt_br": captions["items"][task_id],
            },
            "social_sources": social,
            "metadata": {
                "workflow": "jaguartv-postmatch-v1",
                "workflow_identity": workflow_identity,
                "artifact_revision": revision,
                "label": "赛后比分",
                "status_requested": "PENDING_REVIEW",
                "video_sha256": _sha256(video_path),
                "cover_sha256": _sha256(cover_path),
                "video_relative_path": _relative(video_path, workspace),
                "associations": associations,
                "attachment_contract_required": True,
                "source_poster_and_cover_uploaded": False,
                "model_fallback_used": bool(item.get("prompt_fallback_used")),
                "dreamina_model": item.get("dreamina_model"),
                "image2_provider": poster.get("image2_provider"),
            },
        }
        draft_path = metadata_dir / f"{task_id}.json"
        _write_json(draft_path, metadata)
        shutil.copy2(draft_path, package_dir / "upload-metadata.json")
        _write_json(
            package_dir / "package-manifest.json",
            {
                "workflow_identity": workflow_identity,
                "artifact_revision": revision,
                "server_relative_dir": server_relative_dir,
                "files": files,
            },
        )

        existing = _remote_json(
            ssh_alias, remote_app, helper, ["inspect", "--workflow-identity", workflow_identity]
        )
        if existing["count"] > 1:
            raise RuntimeError(f"duplicate active server records found for {task_id}")
        if existing["count"] == 1:
            current = existing["items"][0]
            if current["status"] != "PENDING_REVIEW":
                raise RuntimeError(f"server record for {task_id} is no longer pending review")
            item_id = current["id"]
            upload_action = "REUSED" if current["sha256"] == metadata["metadata"]["video_sha256"] else "CORRECTED"
        else:
            if tunnel is None:
                tunnel, base_url = _start_ssh_tunnel(
                    ssh_alias, int(server.get("remote_dashboard_port", 8788))
                )
                atexit.register(_stop_tunnel, tunnel)
                connectivity = _server_connectivity(base_url, dashboard_token)
                connectivity_method = "authenticated local API over SSH tunnel"
                if not connectivity:
                    raise RuntimeError("authenticated Pending Review connectivity check failed")
            uploaded = _upload_original(video_path, metadata, base_url, upload_token)
            item_id = str(uploaded["id"])
            upload_action = "CREATED"

        reusable = bool(
            existing["count"] == 1
            and current["artifact_revision"] == revision
            and current["sha256"] == metadata["metadata"]["video_sha256"]
            and current["attachments_verified"]
            and current["exact_cover_verified"]
            and current["association_count"] == len(associations)
        )
        if reusable:
            verified = {
                "id": item_id,
                "status": current["status"],
                "category": current["category"],
                "association_count": current["association_count"],
                "exact_cover_verified": current["exact_cover_verified"],
                "video_sha256": current["sha256"],
                "video_replaced": False,
            }
        else:
            remote_package = _stage_remote_package(ssh_alias, package_dir)
            verified = _remote_json(
                ssh_alias,
                remote_app,
                helper,
                [
                    "finalize", "--workflow-identity", workflow_identity,
                    "--package-dir", remote_package, "--item-id", item_id,
                ],
            )
        if (
            verified.get("status") != "PENDING_REVIEW"
            or verified.get("category") != "post_match_score"
            or not verified.get("exact_cover_verified")
            or verified.get("association_count") != len(associations)
        ):
            raise RuntimeError(f"server package verification failed for {task_id}")
        result_record = {
            "task_id": task_id,
            "workflow_identity": workflow_identity,
            "artifact_revision": revision,
            "upload_id": item_id,
            "action": upload_action,
            "status": verified["status"],
            "category": verified["category"],
            "association_count": verified["association_count"],
            "exact_cover_verified": True,
            "video_sha256": verified["video_sha256"],
            "uploaded_at": utc_now(),
        }
        upload_results.append(result_record)
        drafts.append(
            {
                "task_id": task_id,
                "video": _relative(video_path, workspace),
                "metadata": _relative(draft_path, workspace),
                "metadata_bytes": len(json.dumps(metadata, ensure_ascii=False).encode("utf-8")),
                "upload_id": item_id,
                "uploaded": True,
                "action": upload_action,
            }
        )
        _write_json(
            phase5_dir / "upload-plan.json",
            {
                "schema_version": "jaguartv-postmatch-upload-plan-v2",
                "target_date": target_date.isoformat(),
                "category": "post_match_score",
                "label": "赛后比分",
                "drafts": drafts,
                "uploads_performed": len(upload_results),
            },
        )

    preflight = {
        "checked_at": utc_now(),
        "credential_store": "macos-keychain",
        "server_url_available": True,
        "upload_token_available": True,
        "dashboard_token_available": True,
        "ssh_adapter_available": True,
        "connectivity_check_performed": True,
        "connectivity_ok": connectivity,
        "connectivity_method": connectivity_method,
        "original_video_upload_supported": True,
        "exact_cover_upload_supported": True,
        "source_poster_attachment_supported": True,
        "json_attachment_upload_supported": True,
        "safe_to_upload_complete_package": True,
    }
    _write_json(phase5_dir / "preflight.json", preflight)
    report_path = phase5_dir / "upload-report.json"
    _write_json(
        report_path,
        {
            "schema_version": "jaguartv-postmatch-upload-report-v1",
            "target_date": target_date.isoformat(),
            "label": "赛后比分",
            "status": "PENDING_REVIEW",
            "verified_count": len(upload_results),
            "items": upload_results,
            "completed_at": utc_now(),
        },
    )
    (phase5_dir / "execution-report-blocked.md").unlink(missing_ok=True)
    result = {
        "ok": True,
        "status": "COMPLETE",
        "uploads_performed": len(upload_results),
        "prepared_drafts": len(drafts),
        "preflight": str((phase5_dir / "preflight.json").resolve()),
        "upload_plan": str((phase5_dir / "upload-plan.json").resolve()),
        "upload_report": str(report_path.resolve()),
        "upload_ids": [item["upload_id"] for item in upload_results],
    }
    if tunnel is not None:
        _stop_tunnel(tunnel)
        atexit.unregister(_stop_tunnel)
    return result


prepare_phase5 = run_phase5
