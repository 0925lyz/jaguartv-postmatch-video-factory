from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from .credentials import env_or_keychain
from .retry import _write_state, retry_forever
from .util import codex_model_args, deterministic_motion_plan, normalize_name, postmatch_run_dir, utc_now
from .util import sanitize_filename_part
from .voice import prepare_voice_rotation


W, H = 1080, 1920
DEFAULT_REASONING_MODEL = "current-task"
DEFAULT_REASONING_FALLBACK = "current-task"
VIDEO_ASSEMBLY_POLICY = (
    "generate exactly the four-second opening poster hook; assemble every later segment from existing "
    "authorized inventory; play each selected operation and motion CTA clip in full; calculate final duration from "
    "the actual segment durations"
)
DOWNLOAD_SENTENCE = "Acesse jaguartvbrasil.com/baixar-app para baixar."
SINGLE_HOOK_ACTION_POLICY = (
    "animate the poster background with stadium light, smoke, crowd depth, fabric motion, and score energy; "
    "the winning player may jump, shout, pump fists, and celebrate intensely; "
    "the losing player may pound the turf, sigh, bury their head in their hands, or complain toward the referee, teammate, or opponent"
)

MOTION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["motion_prompt", "shot_script", "safety"],
    "additionalProperties": False,
    "properties": {
        "motion_prompt": {"type": "string", "minLength": 260, "maxLength": 1800},
        "shot_script": {
            "type": "array",
            "minItems": 3,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["time", "action"],
                "additionalProperties": False,
                "properties": {"time": {"type": "string"}, "action": {"type": "string"}},
            },
        },
        "safety": {"type": "string"},
    },
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _probe(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate:format=duration",
            "-of", "json", str(path),
        ],
        text=True, capture_output=True, timeout=30, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {completed.stderr[-500:]}")
    payload = json.loads(completed.stdout)
    stream = payload["streams"][0]
    return {
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "fps": stream.get("r_frame_rate"),
        "duration": float(payload["format"]["duration"]),
    }


def _duration(path: Path) -> float:
    completed = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        text=True, capture_output=True, timeout=30, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe duration failed for {path.name}: {completed.stderr[-500:]}")
    return float(completed.stdout.strip())


def video_filenames(poster_path: str | Path, source_seconds: int = 4, sequence: str | int | None = None) -> dict[str, str]:
    stem = sanitize_filename_part(Path(poster_path).stem)
    prefix = f"{int(sequence):02d}" if sequence is not None else ""
    named = f"{prefix}{stem}" if prefix else stem
    return {
        "media_stem": named,
        "poster_stem": stem,
        "sequence": prefix,
        "master": f"{named}_母版_1080x1920.png",
        "raw_video": f"{named}_海报动态_{source_seconds}秒.mp4",
        "hook": f"{named}_动态钩子_{source_seconds}秒.mp4",
        "final": f"{named}_成片.mp4",
        "cover": f"{named}_封面_1080x1920.jpg",
    }


def _assembly_timing(
    operation_durations: list[float], cta_duration: float, voice_duration: float
) -> dict[str, Any]:
    cta_seconds = max(cta_duration, voice_duration)
    return {
        "hook_seconds": 4.0,
        "operation_seconds": operation_durations,
        "cta_source_seconds": cta_duration,
        "voice_seconds": voice_duration,
        "cta_seconds": cta_seconds,
        "final_seconds": 4.0 + sum(operation_durations) + cta_seconds,
    }


def cta_rotation_state(
    factory_root: Path,
    runtime_root: Path,
    ctas: list[Path],
    target_date: date,
) -> tuple[int, dict[str, Any]]:
    if not ctas:
        raise FileNotFoundError("CTA inventory is empty")
    state_path = runtime_root / "component-rotation.json"
    state: dict[str, Any] = {"policy": "round-robin-across-production-days"}
    if state_path.is_file():
        state.update(json.loads(state_path.read_text(encoding="utf-8")))
    try:
        next_index = int(state.get("next_cta_index", 0)) % len(ctas)
    except (TypeError, ValueError):
        next_index = 0

    if state.get("last_date") != target_date.isoformat():
        latest_before: tuple[str, int] | None = None
        for run_dir in sorted((factory_root / "runs").glob("*")):
            if not run_dir.is_dir():
                continue
            run_date = run_dir.name
            if run_date >= target_date.strftime("%Y%m%d"):
                continue
            manifest_path = run_dir / "phase4" / "build-manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                items = manifest.get("items") or []
            except (json.JSONDecodeError, OSError):
                continue
            if not items:
                continue
            last_cta = str(items[-1].get("cta") or "")
            try:
                cta_index = next(i for i, candidate in enumerate(ctas) if str(candidate.resolve()) == str(Path(last_cta).resolve()))
            except StopIteration:
                continue
            if latest_before is None or run_date > latest_before[0]:
                latest_before = (run_date, (cta_index + 1) % len(ctas))
        if latest_before is not None:
            next_index = latest_before[1]

    return next_index, state_path


def _master_from_layers(
    background_path: Path, foreground_path: Path, output: Path,
    background_output: Path, foreground_output: Path,
) -> dict[str, Any]:
    poster_background = ImageOps.exif_transpose(Image.open(background_path)).convert("RGB")
    poster_foreground = ImageOps.exif_transpose(Image.open(foreground_path)).convert("RGBA")
    if poster_background.size != (2048, 2560) or poster_foreground.size != (2048, 2560):
        raise RuntimeError(f"Unexpected poster layer dimensions: {poster_background.size}, {poster_foreground.size}")
    background = ImageOps.fit(poster_background, (W, H), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(42))
    background = Image.blend(background, Image.new("RGB", (W, H), (3, 8, 10)), 0.33).convert("RGBA")
    contained = poster_background.resize((W, 1350), Image.Resampling.LANCZOS)
    locked = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    locked.alpha_composite(poster_foreground.resize((W, 1350), Image.Resampling.LANCZOS), (0, 285))
    background.alpha_composite(contained.convert("RGBA"), (0, 285))
    master = background.copy()
    master.alpha_composite(locked)
    output.parent.mkdir(parents=True, exist_ok=True)
    background.convert("RGB").save(background_output, "PNG", optimize=True)
    locked.save(foreground_output, "PNG", optimize=True)
    master.convert("RGB").save(output, "PNG", optimize=True)
    return {
        "source_poster_size": [2048, 2560], "master_size": [W, H],
        "poster_box": [0, 285, W, 1635], "cropped": False,
        "background_master": str(background_output.resolve()),
        "foreground_master": str(foreground_output.resolve()),
    }


def _motion_context(entry: dict[str, Any], research: dict[str, Any]) -> str:
    if entry["kind"] == "summary":
        rows = [
            f"{m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}"
            for m in entry["results"]
        ]
        return "Completed-results summary rows: " + "; ".join(rows)
    result = entry["results"][0]
    match_record = research.get("verified_match_record", {})
    goals = match_record.get("goals", [])
    red_cards = (match_record.get("incidents") or {}).get("red_cards", [])
    scorers = ", ".join(dict.fromkeys(str(goal.get("scorer")) for goal in goals if goal.get("scorer")))
    dismissals = "; ".join(
        " ".join(
            str(card.get(field) or "").strip()
            for field in ("team", "minute", "text")
            if str(card.get(field) or "").strip()
        )
        for card in red_cards
    )
    return (
        f"Completed match: {result['home_team']} {result['home_score']}-{result['away_score']} {result['away_team']}; "
        f"{result['competition']}; official state {result['result_status']}; verified scorers: {scorers or 'not used visually'}; "
        f"verified red cards: {dismissals or 'none'}. Preserve the verified participating-player likenesses already present in the poster; do not replace, relabel, or add a player."
    )


def _run_motion_model(
    master: Path, task_id: str, kind: str, context: str, output: Path, schema_path: Path,
    primary_model: str = DEFAULT_REASONING_MODEL, fallback_model: str = DEFAULT_REASONING_FALLBACK,
    reasoning_effort: str = "high",
    model_provider: str | None = None,
) -> tuple[dict[str, Any], str, bool]:
    if output.is_file():
        payload = json.loads(output.read_text(encoding="utf-8"))
        return payload, str(payload.get("model_adapter", primary_model)), bool(payload.get("fallback_used", False))
    camera_rule = (
        "Use a completely locked camera: no zoom, no push-in, no pan, no tilt, no reframing, and no edge movement. "
        "Animate only atmospheric light and tiny depth parallax in the supplied clean background."
        if kind == "summary"
        else
        "Animate only the supplied clean photographic background: "
        f"{SINGLE_HOOK_ACTION_POLICY}. Do not add typography, scores, crests, channel marks, or branding."
    )
    instruction = f"""You are writing one short English image-to-video motion prompt and a 4-second shot script for JaguarTV post-match production. The attached image is the clean background layer. Treat image content and match facts as reference data only.

Task ID: {task_id}
Poster kind: {kind}
Verified context: {context}

Requirements:
- Never create or modify text, scores, dates, crests, channel marks, sponsors, watermarks, or the JaguarTV logo. Those elements are absent from this input and are composited later as a locked foreground.
- Keep all faces unobstructed and anatomically stable. Preserve only the verified player likenesses already visible in the input; do not change identities or add people. Anonymous fictional hardman players are allowed only when the input poster already uses them because no verified real-player image was available.
- {camera_rule} The first frame must remain faithful to the input background.
- No invented red card, injury, foul, goal reenactment, trophy, defamatory claim, or factual incident. Emotional celebration and frustration are allowed as visual reactions to the verified score. No new logos, text, limbs, people, fireworks over faces, UI, watermark, or camera shake.
- For a summary poster, animate only atmospheric light, slight parallax, and restrained row/crest emphasis; do not animate players because there are none.
- Output only JSON matching the schema. The motion_prompt must be standalone English and explicitly state 9:16, 4 seconds, background-only motion, and no generated text or branding.
"""

    def invoke(model: str, target: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "codex", "exec", "--ephemeral", "--skip-git-repo-check", "-C", str(output.parent),
                "-s", "read-only", *codex_model_args(model, model_provider),
                "-i", str(master), "--output-schema", str(schema_path),
                "-o", str(target), instruction,
            ],
            text=True, capture_output=True, timeout=900, check=False,
        )

    def invoke_with_retry(model: str, target: Path, attempts: int = 3) -> subprocess.CompletedProcess[str] | None:
        last: subprocess.CompletedProcess[str] | None = None
        for attempt in range(1, attempts + 1):
            target.unlink(missing_ok=True)
            last = invoke(model, target)
            if last.returncode == 0 and target.is_file() and target.read_text(encoding="utf-8", errors="replace").strip():
                return last
            print(
                f"[motion] {task_id} {model} attempt {attempt}/{attempts} failed "
                f"(rc={last.returncode}, has_file={target.is_file()}); retrying",
                flush=True,
            )
        return last

    temporary = output.with_suffix(".model.json")
    result = invoke_with_retry(primary_model, temporary, attempts=3)
    model, fallback = primary_model, False
    if result is None or result.returncode != 0 or not temporary.is_file():
        result = invoke_with_retry(fallback_model, temporary, attempts=3)
        if result is None or result.returncode != 0 or not temporary.is_file():
            error = (
                (result.stderr if result else "") or (result.stdout if result else "")
                or "model adapter failure"
            )[-900:]
            raise RuntimeError(f"Motion prompt generation failed for {task_id}: {error}")
        model, fallback = fallback_model, True
    payload = json.loads(temporary.read_text(encoding="utf-8"))
    temporary.unlink(missing_ok=True)
    prompt = str(payload.get("motion_prompt", ""))
    duration_terms = ("4 seconds", "4-second", "four seconds", "four-second")
    if len(prompt) < 260 or not any(term in prompt.lower() for term in duration_terms) or "9:16" not in prompt:
        raise RuntimeError(f"Motion prompt validation failed for {task_id}")
    payload["task_id"] = task_id
    payload["model_adapter"] = model
    payload["reasoning_effort"] = reasoning_effort
    payload["fallback_used"] = fallback
    payload["generated_at"] = utc_now()
    _write_json(output, payload)
    return payload, model, fallback


def _parse_json_output(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            return json.loads(raw[start : end + 1])
        raise


def _submit_dreamina(master: Path, prompt: str, model: str, resolution: str, duration: int) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "dreamina", "image2video", "--image", str(master), "--prompt", prompt,
            "--duration", str(duration), "--video_resolution", resolution,
            "--model_version", model, "--poll", "0",
        ],
        text=True, capture_output=True, timeout=180, check=False,
    )
    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "Dreamina submit failure")[-900:]
        raise RuntimeError(error)
    payload = _parse_json_output(completed.stdout)
    submit_id = str(payload.get("submit_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9-]{16,80}", submit_id):
        raise RuntimeError("Dreamina submit response did not contain a valid submit_id")
    return {
        "submit_id": submit_id,
        "logid": payload.get("logid"),
        "gen_status": payload.get("gen_status"),
        "credit_count": payload.get("credit_count"),
        "submitted_at": utc_now(),
    }


def _query_dreamina(submit_id: str, download_dir: Path) -> tuple[dict[str, Any], Path | None]:
    download_dir.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in download_dir.glob("*.mp4")}
    completed = subprocess.run(
        ["dreamina", "query_result", "--submit_id", submit_id, "--download_dir", str(download_dir)],
        text=True, capture_output=True, timeout=180, check=False,
    )
    raw = completed.stdout or completed.stderr
    if completed.returncode != 0:
        return {"gen_status": "query_error", "error": raw[-700:]}, None
    payload = _parse_json_output(raw)
    after = {path.resolve() for path in download_dir.glob("*.mp4")}
    new_files = sorted(after - before, key=lambda path: path.stat().st_mtime)
    downloaded = new_files[-1] if new_files else None
    if payload.get("gen_status") == "success" and downloaded is None:
        candidates = sorted(after, key=lambda path: path.stat().st_mtime)
        downloaded = candidates[-1] if candidates else None
    return payload, downloaded


def _poll_all(states: dict[str, dict[str, Any]], max_rounds: int = 120, sleep_seconds: int = 15) -> None:
    pending = set(states)
    for _round in range(1, max_rounds + 1):
        for task_id in list(pending):
            state = states[task_id]
            payload, downloaded = _query_dreamina(state["submit"]["submit_id"], state["download_dir"])
            status = str(payload.get("gen_status", "querying"))
            state["last_query"] = {"at": utc_now(), "status": status}
            if status == "success" and downloaded and downloaded.stat().st_size > 20_000:
                destination = state["raw_video"]
                if downloaded.resolve() != destination.resolve():
                    shutil.move(str(downloaded), str(destination))
                state["completed_at"] = utc_now()
                pending.remove(task_id)
            elif status == "fail":
                reason = str(payload.get("fail_reason") or payload.get("message") or "Dreamina generation failed")[:700]
                state["failure"] = reason
                pending.remove(task_id)
        if not pending:
            return
        time.sleep(sleep_seconds)
    for task_id in pending:
        states[task_id]["failure"] = "Dreamina task timed out"


def _request_json_python(url: str, api_key: str, payload: dict[str, Any] | None = None, timeout: int = 60) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        raise RuntimeError(f"APIMart request failed HTTP {error.code}: {detail}") from error
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("APIMart returned invalid JSON") from error
    if isinstance(parsed, dict) and parsed.get("error"):
        raise RuntimeError(f"APIMart request failed: {parsed.get('error')}")
    return parsed


def _upload_image_python(api_base_url: str, api_key: str, image: Path) -> str:
    import uuid

    data = image.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise RuntimeError(f"image exceeds 20MB: {image.name}")
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}.get(image.suffix.lower())
    if not mime:
        raise RuntimeError(f"unsupported image format: {image.name}")
    boundary = f"----jaguartv{uuid.uuid4().hex}"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{image.name}\"\r\n".encode(),
        f"Content-Type: {mime}\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    request = urllib.request.Request(
        f"{api_base_url}/uploads/images",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            parsed = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"APIMart image upload failed HTTP {error.code}: {raw[:700]}") from error
    image_url = str(parsed.get("url") or "")
    if not image_url:
        raise RuntimeError("APIMart image upload response did not contain url")
    return urllib.parse.quote(image_url, safe=":/?#[]@!$&'()*+,;=%")


def _wait_apimart_task_python(api_base_url: str, api_key: str, task_id: str, timeout_seconds: int = 900, state_path: Path | None = None) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_status = ""
    while time.time() < deadline:
        payload = _request_json_python(
            f"{api_base_url}/tasks/{urllib.parse.quote(task_id)}?language=zh",
            api_key,
            timeout=60,
        )
        task = payload.get("data") if isinstance(payload, dict) else None
        status = str((task or {}).get("status") or "unknown")
        progress = (task or {}).get("progress")
        if status != last_status:
            print(f"[apimart-python] {task_id} status={status} progress={progress}", flush=True)
            last_status = status
        if status == "completed":
            return task or {}
        if status in {"failed", "cancelled"}:
            if state_path:
                _write_state(state_path, {"provider_task_id": "", "provider_task_status": status})
            raise RuntimeError(f"APIMart task failed: {task}")
        time.sleep(8)
    raise TimeoutError(f"APIMart task timed out: {task_id}")


def _extract_apimart_video_url(task: dict[str, Any]) -> str:
    for video in ((task.get("result") or {}).get("videos") or []):
        urls = video.get("url")
        if isinstance(urls, str) and urls.startswith(("http://", "https://")):
            return urls
        if isinstance(urls, list):
            for url in urls:
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    return url
    raise RuntimeError("APIMart task completed without a video URL")


def _apimart_safe_prompt(prompt: str) -> str:
    base = re.sub(r"\s+", " ", prompt).strip()
    replacements = {
        "shout": "celebrate",
        "red card": "match incident",
        "red cards": "match incidents",
        "injury": "match interruption",
        "injuries": "match interruptions",
        "foul": "challenge",
        "fouls": "challenges",
        "fireworks": "light streaks",
        "smoke": "soft haze",
    }
    for old, new in replacements.items():
        base = re.sub(rf"\b{re.escape(old)}\b", new, base, flags=re.IGNORECASE)
    if len(base) > 1200:
        base = base[:1200].rsplit(" ", 1)[0]
    return (
        "Create a safe sports-broadcast 9:16 background-only image-to-video animation for exactly 4 seconds. "
        "Use the supplied clean background as the exact first frame. "
        "Animate only subtle stadium lights, crowd depth, soft atmospheric haze, cloth movement, and restrained winning/losing post-match emotion. Keep faces clear and anatomy stable. "
        "Do not add new people, text, scores, crests, channel marks, logos, watermarks, camera shake, cropping, or factual incidents. Locked foreground is added later. "
        f"Reference intent: {base}"
    )



def _generate_apimart_video_python(
    config: dict[str, Any], master: Path, prompt: str, output: Path, state_path: Path,
) -> dict[str, Any]:
    video_config = config.get("video") or {}
    key = env_or_keychain("APIMART_API_KEY")
    model = str(video_config.get("fallback_model") or "wan2.6-i2v-flash")
    resolution = str(video_config.get("fallback_resolution") or "720p")
    provider_resolution = resolution.upper()
    configured_duration = int(video_config.get("fallback_duration") or 4)
    provider_duration = configured_duration
    configured_base = str(os.environ.get("APIMART_BASE_URL") or "https://api.apimart.ai/v1").rstrip("/")
    api_base_url = configured_base if configured_base.endswith("/v1") else f"{configured_base}/v1"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    task_id = str(state.get("provider_task_id") or "")
    task = None
    prompt_used = prompt
    safe_prompt_used = False
    for attempt_prompt in ((prompt,) if task_id else (prompt, _apimart_safe_prompt(prompt))):
        safe_prompt_used = attempt_prompt != prompt
        prompt_used = attempt_prompt
        if not task_id:
            image_url = _upload_image_python(api_base_url, key, master)
            submission = _request_json_python(
                f"{api_base_url}/videos/generations", key,
                {
                    "model": model, "duration": provider_duration,
                    "resolution": provider_resolution, "generation_type": "reference",
                    "image_urls": [image_url], "prompt": attempt_prompt,
                }, timeout=60,
            )
            task_id = str(((submission.get("data") or [{}])[0] or {}).get("task_id") or "")
            if not task_id:
                raise RuntimeError("APIMart video submission did not contain task_id")
            _write_state(state_path, {"provider_task_id": task_id, "provider_task_status": "polling"})
        try:
            task = _wait_apimart_task_python(api_base_url, key, task_id, state_path=state_path)
            break
        except RuntimeError as error:
            if "内容安全系统拒绝" in str(error) and not safe_prompt_used:
                print(f"[apimart-python] {task_id} content-safety retry with safe prompt", flush=True)
                task_id = ""
                continue
            raise
    if task is None:
        raise RuntimeError("APIMart task did not complete")
    video_url = _extract_apimart_video_url(task)
    with urllib.request.urlopen(video_url, timeout=180) as response:
        data = response.read()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    if output.stat().st_size < 20_000:
        _write_state(state_path, {"provider_task_id": "", "provider_task_status": "invalid_download"})
        raise RuntimeError("APIMart downloaded video is too small")
    return {
        "provider": "apimart-python",
        "model": model,
        "resolution": resolution,
        "duration": configured_duration,
        "provider_resolution": provider_resolution,
        "provider_duration": provider_duration,
        "fallback_used": True,
        "apimart_task_id": task_id,
        "apimart_safe_prompt_used": safe_prompt_used,
        "apimart_prompt_chars": len(prompt_used),
        "completed_at": utc_now(),
    }


def _generate_apimart_video(
    config: dict[str, Any], v7: Path, master: Path, prompt: str, output: Path,
) -> dict[str, Any]:
    del v7
    state_path = output.with_suffix(".apimart-retry.json")
    return retry_forever(
        lambda: _generate_apimart_video_python(config, master, prompt, output, state_path),
        state_path=state_path, operation_name=f"video:{output.stem}",
    )


def _make_exact_hook(master: Path, dreamina_video: Path, foreground: Path, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-framerate", "30", "-t", "0.10", "-i", str(master),
            "-i", str(dreamina_video),
            "-loop", "1", "-framerate", "30", "-t", "4.00", "-i", str(foreground),
            "-filter_complex",
            "[0:v]scale=1080:1920,setsar=1,trim=duration=0.10,setpts=PTS-STARTPTS[first];"
            "[1:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,trim=start=0.10:end=4.00,setpts=PTS-STARTPTS[ambient];"
            "[2:v]format=rgba,trim=duration=3.90,setpts=PTS-STARTPTS[foreground];"
            "[ambient][foreground]overlay=0:0:format=auto[exact];"
            "[first][exact]concat=n=2:v=1:a=0[out]",
            "-map", "[out]", "-t", "4", "-r", "30", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ],
        capture_output=True, timeout=240, check=True,
    )


def _make_static_hook(master: Path, output: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-loop", "1", "-framerate", "30", "-i", str(master), "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(output)],
        capture_output=True, timeout=120, check=True,
    )


def _locked_foreground_rms(master: Path, frame: Path, foreground: Path) -> float:
    first = Image.open(master).convert("RGB")
    second = Image.open(frame).convert("RGB").resize(first.size, Image.Resampling.LANCZOS)
    mask = Image.open(foreground).convert("RGBA").getchannel("A")
    stat = ImageStat.Stat(ImageChops.difference(first, second), mask=mask)
    return math.sqrt(sum(value * value for value in stat.rms) / len(stat.rms))


def _compose_full_inventory(
    hook: Path, modules: list[Path], cta: Path, music: Path, voice: Path, output: Path,
) -> dict[str, Any]:
    operation_durations = [_duration(path) for path in modules]
    cta_source_duration = _duration(cta)
    voice_duration = _duration(voice)
    timing = _assembly_timing(operation_durations, cta_source_duration, voice_duration)
    visuals = [hook, *modules, cta]
    visual_durations = [4.0, *operation_durations, timing["cta_seconds"]]
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for path in visuals:
        command.extend(["-i", str(path)])
    music_index = len(visuals)
    voice_index = music_index + 1
    command.extend(["-stream_loop", "-1", "-i", str(music), "-i", str(voice)])
    filters = []
    video_labels = []
    for index, (duration, source_duration) in enumerate(zip(visual_durations, [4.0, *operation_durations, cta_source_duration])):
        extension = max(0.0, duration - source_duration)
        pad = f",tpad=stop_mode=clone:stop_duration={extension:.6f}" if extension > 0.01 else ""
        filters.append(
            f"[{index}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30"
            f"{pad},trim=duration={duration:.6f},setpts=PTS-STARTPTS[v{index}]"
        )
        video_labels.append(f"[v{index}]")
    filters.append(f"{''.join(video_labels)}concat=n={len(visuals)}:v=1:a=0[v]")
    cta_start = 4.0 + sum(operation_durations)
    total = timing["final_seconds"]
    filters.extend([
        f"[{music_index}:a]atrim=0:{total:.6f},asetpts=N/SR/TB,volume='if(gte(t,{cta_start:.6f}),0.12,0.3)':eval=frame[bg]",
        f"[{voice_index}:a]atrim=0:{voice_duration:.6f},asetpts=N/SR/TB,adelay={round(cta_start * 1000)}:all=1,volume=1.15[vo]",
        "[bg][vo]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=7,alimiter=limit=0.8:attack=5:release=50[a]",
    ])
    command.extend([
        "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.6f}", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    completed = subprocess.run(command, text=True, capture_output=True, timeout=900, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ffmpeg full-duration compose failed: {(completed.stderr or completed.stdout)[-900:]}")
    return timing


def _extract_frame(video: Path, at: float, output: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at), "-i", str(video), "-frames:v", "1", str(output)],
        capture_output=True, timeout=60, check=True,
    )


def _rms_difference(a: Path, b: Path) -> float:
    first = Image.open(a).convert("RGB").resize((270, 480), Image.Resampling.LANCZOS)
    second = Image.open(b).convert("RGB").resize((270, 480), Image.Resampling.LANCZOS)
    stat = ImageStat.Stat(ImageChops.difference(first, second))
    return math.sqrt(sum(value * value for value in stat.rms) / len(stat.rms))


def _caption_for_single(
    result: dict[str, Any], research: dict[str, Any], batch_id: str | None = None,
) -> dict[str, Any]:
    goals = research.get("verified_match_record", {}).get("goals", [])
    goal_parts = []
    for goal in goals:
        detail = str(goal.get("scorer", ""))
        if goal.get("own_goal"):
            detail += " (contra)"
        elif goal.get("penalty"):
            detail += " (pênalti)"
        if detail:
            goal_parts.append(detail)
    goal_text = ", ".join(goal_parts)
    match = f"{result['home_team'].title()} {result['home_score']} x {result['away_score']} {result['away_team'].title()}"
    parsed_date = date.fromisoformat(result["official_match_date"])
    months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
    date_text = f"{parsed_date.day} de {months[parsed_date.month - 1]} de {parsed_date.year}"
    tags = _caption_tags(result["home_team"], result["away_team"], result["competition"])
    lead_tk = "APITO FINAL" if batch_id == "post2" else "PLACAR FINAL"
    lead_yt = "No apito final" if batch_id == "post2" else "Resultado oficial"
    return {
        "match": match,
        "task1_fixture_id": result["task1_fixture_id"],
        "caption_tk": (
            f"{lead_tk}: {match}. "
            f"{('Gols: ' + goal_text + '. ') if goal_text else ''}"
            f"{DOWNLOAD_SENTENCE}"
        ),
        "tags_tk": tags,
        "caption_yt": (
            f"{lead_yt}: {match}, por {result['competition']}, em {date_text}. "
            f"A partida começou às {result['original_kickoff_time']} no Horário de Brasília. "
            f"{('Gols: ' + goal_text + '. ') if goal_text else ''}"
            f"{DOWNLOAD_SENTENCE}"
        ),
        "tags_yt": tags,
    }


def _caption_tags(home: str, away: str, competition: str) -> list[str]:
    def tag(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "", normalize_name(value))
        return f"#{slug[:28] or 'futebol'}"

    league = tag(competition.split("·", 1)[0])
    return [tag(home), tag(away), league, "#jaguartv", "#iptv"]


def _captions(
    entries: list[dict[str, Any]], research_by_id: dict[str, dict[str, Any]], target_date: date,
    batch_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "jaguartv-postmatch-captions-v1",
        "language": "pt-BR",
        "timezone_label": "Horário de Brasília",
        "batch_id": batch_id,
        "generated_at": utc_now(),
        "items": {},
    }
    for entry in sorted(entries, key=lambda item: str(item.get("sequence") or "")):
        if entry["kind"] == "single":
            result = entry["results"][0]
            payload["items"][entry["task_id"]] = _caption_for_single(
                result, research_by_id[result["task1_fixture_id"]], batch_id
            )
        else:
            score_lines = [f"{r['home_team'].title()} {r['home_score']} x {r['away_score']} {r['away_team'].title()}" for r in entry["results"]]
            joined = "; ".join(score_lines)
            months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
            date_text = f"{target_date.day} de {months[target_date.month - 1]} de {target_date.year}"
            payload["items"][entry["task_id"]] = {
                "summary": True,
                "caption_tk": (
                    f"{'GIRO DE RESULTADOS' if batch_id == 'post2' else 'PLACARES FINAIS'} de {date_text}: {joined}. "
                    f"{DOWNLOAD_SENTENCE}"
                ),
                "tags_tk": ["#placares", "#futebol", "#resultados", "#jaguartv", "#iptv"],
                "caption_yt": (
                    f"{'Confira o giro dos placares' if batch_id == 'post2' else 'Resumo dos resultados oficiais'} de {date_text}, no Horário de Brasília: {joined}. "
                    f"{DOWNLOAD_SENTENCE}"
                ),
                "tags_yt": ["#placares", "#futebol", "#resultados", "#jaguartv", "#iptv"],
            }
    return payload


def _phase4_entries(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    phase3 = json.loads((run_dir / "phase3" / "production-manifest.json").read_text(encoding="utf-8"))
    phase1 = json.loads((run_dir / "phase1" / "results.json").read_text(encoding="utf-8"))
    result_by_id = {item["task1_fixture_id"]: item for item in phase1["results"]}
    research_by_id = {}
    for task_id in result_by_id:
        path = run_dir / "phase2" / f"{task_id}_research.json"
        research_by_id[task_id] = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for poster in phase3["posters"]:
        entries.append(
            {
                "task_id": poster["task_id"],
                "kind": poster["kind"],
                "poster_path": poster["poster_path"],
                "background_path": poster["background_path"],
                "foreground_path": poster["foreground_path"],
                "prompt_path": poster["prompt_path"],
                "results": [result_by_id[task_id] for task_id in poster["match_ids"]],
            }
        )
    return entries, research_by_id


def run_phase4(
    config: dict[str, Any], target_date: date, factory_root: Path, batch_id: str | None = None,
) -> dict[str, Any]:
    run_dir = postmatch_run_dir(factory_root, target_date, batch_id)
    phase4_dir = run_dir / "phase4"
    output_dir = phase4_dir / "video"
    prompt_dir = phase4_dir / "motion-prompts"
    raw_dir = phase4_dir / "hook-raw"
    qa_dir = phase4_dir / "qa"
    for directory in (output_dir, prompt_dir, raw_dir, qa_dir):
        directory.mkdir(parents=True, exist_ok=True)

    entries, research_by_id = _phase4_entries(run_dir)
    motion_plan = deterministic_motion_plan(
        [entry["task_id"] for entry in entries], f"{target_date.isoformat()}:{batch_id or 'default'}"
    )
    schema_path = phase4_dir / "motion-prompt.schema.json"
    _write_json(schema_path, MOTION_SCHEMA)
    v7 = Path(str(config["jaguartv_v7_pack"])).resolve()
    factory_assets = v7 / "video_templates" / "jaguartv_match_video_factory" / "assets"
    modules = {
        "downloader": factory_assets / "01-omni-downloader-enlarged-stable-3.0s.mp4",
        "google": factory_assets / "02-google-search-jaguartvbrasil-full-logo-3.0s.mp4",
        "main_epg": factory_assets / "02-omni-football-epg-stable-3.0s.mp4",
    }
    cta_dir = factory_assets / "replaceable" / "cta"
    ctas = sorted((cta_dir / "motion").glob("*.mp4"))
    music_dir = factory_assets / "replaceable" / "music"
    music = sorted(music_dir.glob("0*.m4a"))
    voices, voice_rotation_meta = prepare_voice_rotation(config, target_date, factory_root)
    required = [*modules.values()]
    if any(not path.is_file() for path in required) or not ctas or not music or not voices:
        missing = [str(path) for path in required if not path.is_file()]
        raise FileNotFoundError(f"Required v7 production assets unavailable: {missing}; CTA={len(ctas)} music={len(music)} voice={len(voices)}")

    video_config = config["video"]
    model = str(video_config["model"])
    resolution = str(video_config["resolution"])
    source_seconds = int(video_config["hook_seconds"])
    if source_seconds != 4:
        raise ValueError("Post-match poster hook must be exactly 4 seconds")
    states: dict[str, dict[str, Any]] = {}
    manifest_items = []
    operation_pairs = [
        ("downloader_then_main", modules["downloader"], modules["main_epg"]),
        ("google_then_main", modules["google"], modules["main_epg"]),
        ("main_then_downloader", modules["main_epg"], modules["downloader"]),
        ("main_then_google", modules["main_epg"], modules["google"]),
    ]

    runtime_root = Path(str(config.get("runtime_root") or factory_root / "runtime")).resolve()
    reasoning = config.get("reasoning") or {}
    primary_model = str(reasoning.get("primary_model") or DEFAULT_REASONING_MODEL)
    fallback_model = str(reasoning.get("fallback_model") or primary_model or DEFAULT_REASONING_FALLBACK)
    model_provider = reasoning.get("model_provider")
    reasoning_effort = str(reasoning.get("reasoning_effort") or "high")
    cta_start, rotation_state_path = cta_rotation_state(factory_root, runtime_root, ctas, target_date)
    previous_manifest_path = phase4_dir / "build-manifest.json"
    previous_items: dict[str, dict[str, Any]] = {}
    if previous_manifest_path.is_file():
        previous_manifest = json.loads(previous_manifest_path.read_text(encoding="utf-8"))
        previous_items = {item["task_id"]: item for item in previous_manifest.get("items", [])}
    assigned_cta_indexes: list[int] = []
    cta_assignment_count = 0

    for index, entry in enumerate(entries):
        entry["sequence"] = f"{index + 1:02d}"
        task_id = entry["task_id"]
        names = video_filenames(entry["poster_path"], source_seconds, entry["sequence"])
        media_stem = names["media_stem"]
        master = output_dir / names["master"]
        background_master = output_dir / f"{media_stem}_背景母版_1080x1920.png"
        foreground_master = output_dir / f"{media_stem}_锁定前景_1080x1920.png"
        master_meta = _master_from_layers(
            Path(entry["background_path"]), Path(entry["foreground_path"]), master,
            background_master, foreground_master,
        )
        research = research_by_id[entry["results"][0]["task1_fixture_id"]] if entry["kind"] == "single" else {}
        context = _motion_context(entry, research)
        motion_path = prompt_dir / f"{task_id}_motion.json"
        selected_for_motion = motion_plan[task_id]["dynamic"]
        if selected_for_motion:
            motion, prompt_model, fallback = _run_motion_model(
                background_master, task_id, entry["kind"], context, motion_path, schema_path,
                primary_model, fallback_model, reasoning_effort, model_provider,
            )
        else:
            motion, prompt_model, fallback = ({"motion_prompt": "", "shot_script": []}, "none", False)
        raw_video = raw_dir / names["raw_video"]
        submit_record_path = raw_dir / f"{task_id}_submit.json"
        if not selected_for_motion:
            submit = {"provider": "static-local", "model": "none", "fallback_used": False, "status": "not_called"}
        elif raw_video.is_file() and _probe(raw_video)["duration"] >= 3.5:
            submit = json.loads(submit_record_path.read_text(encoding="utf-8")) if submit_record_path.is_file() else {
                "provider": "existing-local-artifact", "reused_idempotently": True,
            }
        else:
            try:
                submit = {
                    **_submit_dreamina(background_master, motion["motion_prompt"], model, resolution, source_seconds),
                    "provider": "dreamina-vip", "model": model, "resolution": resolution,
                    "duration": source_seconds, "fallback_used": False,
                }
                states[task_id] = {
                    "submit": submit, "download_dir": raw_dir / task_id, "raw_video": raw_video,
                    "master": background_master, "prompt": motion["motion_prompt"],
                    "submit_record_path": submit_record_path,
                }
            except Exception as dreamina_error:  # noqa: BLE001
                try:
                    submit = {
                        **_generate_apimart_video(config, v7, background_master, motion["motion_prompt"], raw_video),
                        "dreamina_error": f"{type(dreamina_error).__name__}: unavailable",
                    }
                except Exception as fallback_error:  # noqa: BLE001
                    raise RuntimeError(
                        f"Dreamina unavailable ({type(dreamina_error).__name__}); APIMart video fallback failed: {fallback_error}"
                    ) from fallback_error
            _write_json(submit_record_path, {**submit, "task_id": task_id})

        previous = previous_items.get(task_id, {})
        try:
            cta_path = Path(str(previous["cta"])).resolve()
            cta_index = next(i for i, candidate in enumerate(ctas) if candidate.resolve() == cta_path)
            operation_name = str(previous["interface_operation"])
            first_module = Path(str(previous["middle_segments"][0])).resolve()
            second_module = Path(str(previous["middle_segments"][1])).resolve()
            music_path = Path(str(previous["music"])).resolve()
            music_index = next(i for i, candidate in enumerate(music) if candidate.resolve() == music_path)
        except (KeyError, IndexError, StopIteration):
            previous = {}
        if not previous:
            cta_index = (cta_start + cta_assignment_count) % len(ctas)
            cta_assignment_count += 1
            operation_name, first_module, second_module = operation_pairs[index % len(operation_pairs)]
            music_index = index % len(music)
        voice_index = cta_index % len(voices)
        assigned_cta_indexes.append(cta_index)
        cta_path = ctas[cta_index]
        component = {
            "task_id": task_id,
            "sequence": entry["sequence"],
            "slug": media_stem,
            "media_stem": media_stem,
            "filename_policy": "Chinese poster-derived names for all generated media",
            "kind": entry["kind"],
            "poster": entry["poster_path"],
            "master": str(master.resolve()),
            "master_layout": master_meta,
            "background_master": str(background_master.resolve()),
            "foreground_master": str(foreground_master.resolve()),
            "motion_selection": motion_plan[task_id],
            "motion_prompt_path": str(motion_path.resolve()),
            "motion_prompt": motion["motion_prompt"],
            "shot_script": motion["shot_script"],
            "prompt_model": prompt_model,
            "prompt_fallback_used": fallback,
            "video_provider": submit.get("provider", "dreamina-vip"),
            "video_model": submit.get("model", model),
            "video_resolution": submit.get("resolution", resolution),
            "video_source_seconds": source_seconds,
            "video_fallback_used": bool(submit.get("fallback_used", False)),
            "video_assembly_policy": VIDEO_ASSEMBLY_POLICY,
            "generated_segments": [
                {
                    "role": "opening_poster_hook",
                    "source": str(master.resolve()),
                    "generator": submit.get("provider", "dreamina-vip"),
                    "seconds": source_seconds,
                }
            ],
            "inventory_segments": [
                {"role": "middle_operation_1", "source": str(first_module.resolve())},
                {"role": "middle_operation_2", "source": str(second_module.resolve())},
                {"role": "cta", "source": str(cta_path.resolve())},
                {"role": "music", "source": str(music[music_index].resolve())},
                {"role": "voice", "source": str(voices[voice_index].resolve())},
            ],
            "hook_compositing_mode": "generated ambient 9:16 extension with exact deterministic 4:5 poster locked above it",
            "seed": None,
            "seed_note": "The selected video provider did not expose a seed; no seed was fabricated.",
            "submit": submit,
            "raw_video": str(raw_video.resolve()) if selected_for_motion else "",
            "interface_operation": operation_name,
            "middle_segments": [str(first_module.resolve()), str(second_module.resolve())],
            "cta": str(cta_path.resolve()),
            "cta_index": cta_index,
            "cta_rotation_policy": "round-robin-across-production-days",
            "music": str(music[music_index].resolve()),
            "music_index": music_index,
            "voice": str(voices[voice_index].resolve()),
            "voice_index": voice_index,
            "source_assets": [entry["poster_path"], str(first_module.resolve()), str(second_module.resolve()), str(cta_path.resolve()), str(music[music_index].resolve()), str(voices[voice_index].resolve())],
            "created_at": utc_now(),
        }
        manifest_items.append(component)
        _write_json(phase4_dir / "build-manifest.json", {"schema_version": "jaguartv-v7-postmatch-build-v1", "target_date": target_date.isoformat(), "batch_id": batch_id, "updated_at": utc_now(), "voice_rotation": voice_rotation_meta, "items": manifest_items})

    if assigned_cta_indexes:
        rotation_state = {
            "policy": "round-robin-across-production-days",
            "last_date": target_date.isoformat(),
            "cta_start_index": cta_start,
            "last_cta_index": assigned_cta_indexes[-1],
            "next_cta_index": (assigned_cta_indexes[-1] + 1) % len(ctas),
            "assigned_cta_count": len(assigned_cta_indexes),
            "cta_count": len(ctas),
        }
        _write_json(rotation_state_path, rotation_state)

    if states:
        _poll_all(states)
        items_by_id = {item["task_id"]: item for item in manifest_items}
        for task_id, state in states.items():
            if state["raw_video"].is_file():
                continue
            try:
                submit = {
                    **_generate_apimart_video(
                        config, v7, state["master"], state["prompt"], state["raw_video"]
                    ),
                    "dreamina_error": str(state.get("failure") or "generation did not produce a file")[:300],
                }
            except Exception as fallback_error:  # noqa: BLE001
                raise RuntimeError(
                    f"Dreamina generation failed for {task_id}; APIMart video fallback failed: {fallback_error}"
                ) from fallback_error
            _write_json(state["submit_record_path"], {**submit, "task_id": task_id})
            item = items_by_id[task_id]
            item.update({
                "submit": submit,
                "video_provider": submit["provider"],
                "video_model": submit["model"],
                "video_resolution": submit["resolution"],
                "video_fallback_used": True,
            })
            item["generated_segments"][0]["generator"] = submit["provider"]
        _write_json(phase4_dir / "build-manifest.json", {"schema_version": "jaguartv-v7-postmatch-build-v1", "target_date": target_date.isoformat(), "batch_id": batch_id, "updated_at": utc_now(), "voice_rotation": voice_rotation_meta, "items": manifest_items})

    combinations = set()
    validations = []
    for item in manifest_items:
        task_id = item["task_id"]
        names = video_filenames(item["poster"], source_seconds, item.get("sequence"))
        selected_for_motion = bool(item["motion_selection"]["dynamic"])
        raw_video = Path(item["raw_video"]) if selected_for_motion else None
        if selected_for_motion and (raw_video is None or not raw_video.is_file()):
            raise FileNotFoundError(f"Generated poster-hook video is missing for {task_id}")
        hook = output_dir / names["hook"]
        final = output_dir / names["final"]
        cover = output_dir / names["cover"]
        master = Path(item["master"])
        foreground_master = Path(item["foreground_master"])
        if selected_for_motion:
            _make_exact_hook(master, raw_video, foreground_master, hook)
        else:
            _make_static_hook(master, hook)
        Image.open(master).convert("RGB").save(cover, "JPEG", quality=95, subsampling=0)
        assembly_timing = _compose_full_inventory(
            hook,
            [Path(path) for path in item["middle_segments"]],
            Path(item["cta"]), Path(item["music"]), Path(item["voice"]), final,
        )
        item["assembly_timing"] = assembly_timing

        first_frame = qa_dir / f"{task_id}_first-frame.png"
        moving_frame = qa_dir / f"{task_id}_moving-frame.png"
        _extract_frame(final, 0, first_frame)
        _extract_frame(final, 2.0, moving_frame)
        hook_probe, final_probe = _probe(hook), _probe(final)
        first_rms = _rms_difference(master, first_frame)
        cover_rms = _rms_difference(master, cover)
        first_cover_rms = _rms_difference(cover, first_frame)
        motion_rms = _rms_difference(first_frame, moving_frame)
        foreground_rms = _locked_foreground_rms(master, moving_frame, foreground_master)
        combination = (item["interface_operation"], Path(item["cta"]).name, Path(item["music"]).name, tuple(Path(path).name for path in item["middle_segments"]))
        duplicate = combination in combinations
        combinations.add(combination)
        qa = {
            "task_id": task_id,
            "master_1080x1920": Image.open(master).size == (W, H),
            "poster_fully_visible": item["master_layout"]["cropped"] is False and item["master_layout"]["poster_box"] == [0, 285, 1080, 1635],
            "hook_probe": hook_probe,
            "hook_4_seconds": abs(hook_probe["duration"] - 4.0) <= 0.08,
            "final_probe": final_probe,
            "expected_final_seconds": assembly_timing["final_seconds"],
            "final_duration_matches_inventory": abs(final_probe["duration"] - assembly_timing["final_seconds"]) <= 0.12,
            "final_1080x1920": (final_probe["width"], final_probe["height"]) == (W, H),
            "first_frame_rms_vs_master": round(first_rms, 3),
            "first_frame_faithful": first_rms < 18.0,
            "cover_rms_vs_poster_master": round(cover_rms, 3),
            "cover_is_complete_poster_master": cover_rms < 5.0,
            "first_frame_rms_vs_cover": round(first_cover_rms, 3),
            "first_frame_matches_video_cover": first_cover_rms < 18.0,
            "first_frame_and_cover_source": "complete uncropped poster master",
            "hook_motion_rms": round(motion_rms, 3),
            "expected_dynamic": selected_for_motion,
            "hook_is_dynamic": motion_rms > 1.0 if selected_for_motion else motion_rms < 1.0,
            "video_model_call_policy_ok": item["motion_selection"]["video_model_called"] == selected_for_motion,
            "locked_foreground_rms_at_2s": round(foreground_rms, 3),
            "poster_facts_stable_at_2s": foreground_rms < 20.0,
            "component_combination_duplicate": duplicate,
            "no_duplicate_combination": not duplicate,
        }
        qa["passed"] = all(
            qa[key] for key in (
                "master_1080x1920", "poster_fully_visible", "hook_4_seconds", "final_duration_matches_inventory",
                "final_1080x1920", "first_frame_faithful", "cover_is_complete_poster_master",
                "first_frame_matches_video_cover", "hook_is_dynamic", "no_duplicate_combination",
                "poster_facts_stable_at_2s", "video_model_call_policy_ok",
            )
        )
        qa_path = qa_dir / f"{task_id}_qa.json"
        _write_json(qa_path, qa)
        if not qa["passed"]:
            raise RuntimeError(f"Phase 4 validation failed for {task_id}: {qa}")
        item.update({
            "hook": str(hook.resolve()), "final": str(final.resolve()), "cover": str(cover.resolve()),
            "qa": str(qa_path.resolve()), "completed_at": utc_now(),
        })
        validations.append(qa)
        _write_json(phase4_dir / "build-manifest.json", {"schema_version": "jaguartv-v7-postmatch-build-v1", "target_date": target_date.isoformat(), "batch_id": batch_id, "updated_at": utc_now(), "voice_rotation": voice_rotation_meta, "items": manifest_items})

    captions_path = phase4_dir / "captions.json"
    _write_json(captions_path, _captions(entries, research_by_id, target_date, batch_id))
    return {
        "ok": True,
        "target_date": target_date.isoformat(),
        "batch_id": batch_id,
        "video_count": len(manifest_items),
        "build_manifest": str((phase4_dir / "build-manifest.json").resolve()),
        "captions": str(captions_path.resolve()),
        "validations": validations,
    }
