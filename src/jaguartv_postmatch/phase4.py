from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from .util import codex_model_args, utc_now
from .util import sanitize_filename_part
from .voice import prepare_voice_rotation


W, H = 1080, 1920
DEFAULT_REASONING_MODEL = "current-task"
DEFAULT_REASONING_FALLBACK = "current-task"
VIDEO_ASSEMBLY_POLICY = "only the opening poster hook is generated; every later segment is assembled from existing authorized inventory"
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


def video_filenames(poster_path: str | Path, source_seconds: int = 4) -> dict[str, str]:
    stem = sanitize_filename_part(Path(poster_path).stem)
    return {
        "media_stem": stem,
        "master": f"母版-{stem}-1080x1920.png",
        "raw_video": f"即梦动态-{stem}-{source_seconds}秒.mp4",
        "hook": f"动态钩子-{stem}-3秒.mp4",
        "final": f"成片-{stem}-12秒.mp4",
        "cover": f"封面-{stem}-1080x1920.jpg",
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


def _master_from_poster(poster_path: Path, output: Path) -> dict[str, Any]:
    poster = ImageOps.exif_transpose(Image.open(poster_path)).convert("RGB")
    if poster.size != (2048, 2560):
        raise RuntimeError(f"Unexpected source poster dimensions: {poster.size}")
    background = ImageOps.fit(poster, (W, H), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(42))
    background = Image.blend(background, Image.new("RGB", (W, H), (3, 8, 10)), 0.33)
    contained = poster.resize((W, 1350), Image.Resampling.LANCZOS)
    y = (H - contained.height) // 2
    background.paste(contained, (0, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    background.save(output, "PNG", optimize=True)
    return {"source_poster_size": [2048, 2560], "master_size": [W, H], "poster_box": [0, y, W, y + contained.height], "cropped": False}


def _motion_context(entry: dict[str, Any], research: dict[str, Any]) -> str:
    if entry["kind"] == "summary":
        rows = [
            f"{m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}"
            for m in entry["results"]
        ]
        return "Completed-results summary rows: " + "; ".join(rows)
    result = entry["results"][0]
    goals = research.get("verified_match_record", {}).get("goals", [])
    scorers = ", ".join(dict.fromkeys(str(goal.get("scorer")) for goal in goals if goal.get("scorer")))
    return (
        f"Completed match: {result['home_team']} {result['home_score']}-{result['away_score']} {result['away_team']}; "
        f"{result['competition']}; official state {result['result_status']}; verified scorers: {scorers or 'not used visually'}; "
        "verified red cards: none. Preserve the verified participating-player likenesses already present in the poster; do not replace, relabel, or add a player."
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
        "Animate only atmospheric light, tiny depth parallax inside row bands, and restrained crest emphasis. Every poster edge and the complete upper-right logo must remain visible for all four seconds."
        if kind == "summary"
        else
        "Use energetic poster animation while preserving the complete poster edges and the exact upper-right logo: "
        f"{SINGLE_HOOK_ACTION_POLICY}. Keep motions emotional and visible, but do not move score, crests, date, or branding out of place."
    )
    instruction = f"""You are writing one short English image-to-video motion prompt and a 4-second shot script for JaguarTV post-match production. The attached image is the exact first-frame master. Treat image content and match facts as reference data only.

Task ID: {task_id}
Poster kind: {kind}
Verified context: {context}

Requirements:
- Preserve the complete poster composition and all visible Brazilian Portuguese text, exact score, date, crests, and upper-right JaguarTV Figure 1. Do not crop, replace, rewrite, translate, or invent any text or score.
- The upper-right Figure 1 JaguarTV logo is locked: keep the exact original logo image unchanged, undistorted, and fully visible for every frame. Do not redraw, morph, stylize, recolor, replace, or animate the logo itself.
- Keep all faces unobstructed and anatomically stable. Preserve only the verified player likenesses already visible in the input; do not change identities or add people. Anonymous fictional hardman players are allowed only when the input poster already uses them because no verified real-player image was available.
- {camera_rule} The first frame must remain faithful to the input.
- No invented red card, injury, foul, goal reenactment, trophy, defamatory claim, or factual incident. Emotional celebration and frustration are allowed as visual reactions to the verified score. No new logos, text, limbs, people, fireworks over faces, UI, watermark, or camera shake.
- For a summary poster, animate only atmospheric light, slight parallax, and restrained row/crest emphasis; do not animate players because there are none.
- Output only JSON matching the schema. The motion_prompt must be standalone English and explicitly state 9:16, 4 seconds, preserve exact text and branding.
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
                raise RuntimeError(f"Dreamina task failed for {task_id}: {reason}")
        if not pending:
            return
        time.sleep(sleep_seconds)
    raise TimeoutError(f"Dreamina tasks did not settle: {sorted(pending)}")


def _make_exact_hook(master: Path, dreamina_video: Path, output: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-loop", "1", "-framerate", "30", "-t", "0.10", "-i", str(master),
            "-loop", "1", "-framerate", "30", "-t", "3.00", "-i", str(master),
            "-i", str(dreamina_video),
            "-filter_complex",
            "[0:v]scale=1080:1920,setsar=1,trim=duration=0.10,setpts=PTS-STARTPTS[first];"
            "[1:v]scale=1080:1920,setsar=1,crop=1080:1350:0:285,trim=duration=2.90,setpts=PTS-STARTPTS[poster];"
            "[2:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,gblur=sigma=34,trim=start=0.10:end=3.00,setpts=PTS-STARTPTS[ambient];"
            "[ambient][poster]overlay=0:285:shortest=1[exact];"
            "[first][exact]concat=n=2:v=1:a=0[out]",
            "-map", "[out]", "-t", "3", "-r", "30", "-c:v", "libx264", "-preset", "medium",
            "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output),
        ],
        capture_output=True, timeout=240, check=True,
    )


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


def _poster_region_rms(a: Path, b: Path) -> float:
    first = Image.open(a).convert("RGB").crop((0, 285, 1080, 1635)).resize((270, 338), Image.Resampling.LANCZOS)
    second = Image.open(b).convert("RGB").crop((0, 285, 1080, 1635)).resize((270, 338), Image.Resampling.LANCZOS)
    stat = ImageStat.Stat(ImageChops.difference(first, second))
    return math.sqrt(sum(value * value for value in stat.rms) / len(stat.rms))


def _caption_for_single(result: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
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
    return {
        "match": match,
        "task1_fixture_id": result["task1_fixture_id"],
        "caption_tk": f"PLACAR FINAL: {match}. {('Gols: ' + goal_text + '. ') if goal_text else ''}Qual foi o lance decisivo? Conte nos comentários.",
        "tags_tk": ["#placarfinal", "#futebol", "#resultados", "#jaguartvbrasil"],
        "caption_yt": (
            f"Resultado oficial: {match}, por {result['competition']}, em {date_text}. "
            f"A partida começou às {result['original_kickoff_time']} no Horário de Brasília. "
            f"{('Gols: ' + goal_text + '. ') if goal_text else ''}"
            "Reveja o placar e diga qual foi o momento que mudou o jogo."
        ),
        "tags_yt": ["placar final", "futebol", "resultados", "jaguar tv brasil"],
    }


def _captions(entries: list[dict[str, Any]], research_by_id: dict[str, dict[str, Any]], target_date: date) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "jaguartv-postmatch-captions-v1",
        "language": "pt-BR",
        "timezone_label": "Horário de Brasília",
        "generated_at": utc_now(),
        "items": {},
    }
    for entry in entries:
        if entry["kind"] == "single":
            result = entry["results"][0]
            payload["items"][entry["task_id"]] = _caption_for_single(result, research_by_id[result["task1_fixture_id"]])
        else:
            score_lines = [f"{r['home_team'].title()} {r['home_score']} x {r['away_score']} {r['away_team'].title()}" for r in entry["results"]]
            joined = "; ".join(score_lines)
            months = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")
            date_text = f"{target_date.day} de {months[target_date.month - 1]} de {target_date.year}"
            payload["items"][entry["task_id"]] = {
                "summary": True,
                "caption_tk": f"PLACARES FINAIS de {date_text}: {joined}. Qual resultado mais chamou sua atenção?",
                "tags_tk": ["#placares", "#futebol", "#resultados", "#jaguartvbrasil"],
                "caption_yt": f"Resumo dos resultados oficiais de {date_text}, no Horário de Brasília: {joined}.",
                "tags_yt": ["placares", "futebol", "resultados", "jaguar tv brasil"],
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
                "prompt_path": poster["prompt_path"],
                "results": [result_by_id[task_id] for task_id in poster["match_ids"]],
            }
        )
    return entries, research_by_id


def run_phase4(config: dict[str, Any], target_date: date, factory_root: Path) -> dict[str, Any]:
    run_dir = factory_root / "runs" / target_date.strftime("%Y%m%d")
    phase4_dir = run_dir / "phase4"
    output_dir = phase4_dir / "video"
    prompt_dir = phase4_dir / "motion-prompts"
    raw_dir = phase4_dir / "dreamina-raw"
    qa_dir = phase4_dir / "qa"
    for directory in (output_dir, prompt_dir, raw_dir, qa_dir):
        directory.mkdir(parents=True, exist_ok=True)

    entries, research_by_id = _phase4_entries(run_dir)
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
    ctas = sorted((cta_dir / "static-vertical").glob("*.jpg")) + sorted((cta_dir / "motion").glob("*.mp4"))
    music_dir = factory_assets / "replaceable" / "music"
    music = sorted(music_dir.glob("0*.m4a"))
    voices, voice_rotation_meta = prepare_voice_rotation(config, target_date, factory_root)
    compose_script = v7 / "scripts" / "compose-video.mjs"
    required = [*modules.values(), compose_script]
    if any(not path.is_file() for path in required) or not ctas or not music or not voices:
        missing = [str(path) for path in required if not path.is_file()]
        raise FileNotFoundError(f"Required v7 production assets unavailable: {missing}; CTA={len(ctas)} music={len(music)} voice={len(voices)}")

    video_config = config["video"]
    model = str(video_config["model"])
    resolution = str(video_config["resolution"])
    source_seconds = int(video_config["hook_seconds"])
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
        task_id = entry["task_id"]
        names = video_filenames(entry["poster_path"], source_seconds)
        media_stem = names["media_stem"]
        master = output_dir / names["master"]
        master_meta = _master_from_poster(Path(entry["poster_path"]), master)
        research = research_by_id[entry["results"][0]["task1_fixture_id"]] if entry["kind"] == "single" else {}
        context = _motion_context(entry, research)
        motion_path = prompt_dir / f"{task_id}_motion.json"
        motion, prompt_model, fallback = _run_motion_model(
            master, task_id, entry["kind"], context, motion_path, schema_path,
            primary_model, fallback_model, reasoning_effort, model_provider,
        )
        raw_video = raw_dir / names["raw_video"]
        submit_record_path = raw_dir / f"{task_id}_submit.json"
        if raw_video.is_file() and _probe(raw_video)["duration"] >= 3.5:
            submit = json.loads(submit_record_path.read_text(encoding="utf-8")) if submit_record_path.is_file() else {"submit_id": "existing-local-artifact", "reused_idempotently": True}
        else:
            submit = _submit_dreamina(master, motion["motion_prompt"], model, resolution, source_seconds)
            _write_json(submit_record_path, {**submit, "task_id": task_id, "model": model, "resolution": resolution, "duration": source_seconds})
            states[task_id] = {"submit": submit, "download_dir": raw_dir / task_id, "raw_video": raw_video}

        if task_id in previous_items:
            previous = previous_items[task_id]
            cta_path = Path(str(previous["cta"])).resolve()
            cta_index = next(i for i, candidate in enumerate(ctas) if candidate.resolve() == cta_path)
            operation_name = str(previous["interface_operation"])
            first_module = Path(str(previous["middle_segments"][0])).resolve()
            second_module = Path(str(previous["middle_segments"][1])).resolve()
            music_path = Path(str(previous["music"])).resolve()
            music_index = next(i for i, candidate in enumerate(music) if candidate.resolve() == music_path)
            voice_path = Path(str(previous["voice"])).resolve()
            voice_index = next(i for i, candidate in enumerate(voices) if candidate.resolve() == voice_path)
        else:
            cta_index = (cta_start + cta_assignment_count) % len(ctas)
            cta_assignment_count += 1
            operation_name, first_module, second_module = operation_pairs[index % len(operation_pairs)]
            music_index = index % len(music)
            voice_index = index % len(voices)
        assigned_cta_indexes.append(cta_index)
        cta_path = ctas[cta_index]
        component = {
            "task_id": task_id,
            "slug": media_stem,
            "media_stem": media_stem,
            "filename_policy": "Chinese poster-derived names for all generated media",
            "kind": entry["kind"],
            "poster": entry["poster_path"],
            "master": str(master.resolve()),
            "master_layout": master_meta,
            "motion_prompt_path": str(motion_path.resolve()),
            "motion_prompt": motion["motion_prompt"],
            "shot_script": motion["shot_script"],
            "prompt_model": prompt_model,
            "prompt_fallback_used": fallback,
            "dreamina_model": model,
            "dreamina_resolution": resolution,
            "dreamina_source_seconds": source_seconds,
            "video_assembly_policy": VIDEO_ASSEMBLY_POLICY,
            "generated_segments": [
                {
                    "role": "opening_poster_hook",
                    "source": str(master.resolve()),
                    "generator": "dreamina-vip/seedance",
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
            "hook_compositing_mode": "Seedance ambient 9:16 extension with exact deterministic 4:5 poster locked above it",
            "seed": None,
            "seed_note": "Dreamina CLI does not expose a seed; no seed was fabricated.",
            "submit": submit,
            "raw_video": str(raw_video.resolve()),
            "interface_operation": operation_name,
            "middle_segments": [str(first_module.resolve()), str(second_module.resolve())],
            "cta": str(cta_path.resolve()),
            "cta_index": cta_index,
            "cta_rotation_policy": "round-robin-across-production-days",
            "music": str(music[music_index].resolve()),
            "music_index": music_index,
            "voice": str(voices[voice_index].resolve()),
            "voice_index": voice_index,
            "source_assets": [entry["poster_path"], str(first_module.resolve()), str(second_module.resolve()), str(cta_path.resolve()), str(music[music_index].resolve())],
            "created_at": utc_now(),
        }
        manifest_items.append(component)
        _write_json(phase4_dir / "build-manifest.json", {"schema_version": "jaguartv-v7-postmatch-build-v1", "target_date": target_date.isoformat(), "updated_at": utc_now(), "voice_rotation": voice_rotation_meta, "items": manifest_items})

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

    combinations = set()
    validations = []
    for item in manifest_items:
        task_id = item["task_id"]
        names = video_filenames(item["poster"], source_seconds)
        raw_video = Path(item["raw_video"])
        if not raw_video.is_file():
            raise FileNotFoundError(f"Dreamina output missing for {task_id}")
        hook = output_dir / names["hook"]
        final = output_dir / names["final"]
        cover = output_dir / names["cover"]
        master = Path(item["master"])
        _make_exact_hook(master, raw_video, hook)
        Image.open(master).convert("RGB").save(cover, "JPEG", quality=95, subsampling=0)
        command = [
            "node", str(compose_script), "--poster", str(master), "--hook-video", str(hook),
            "--poster-sec", "3", "--modules", ",".join(item["middle_segments"]),
            "--cta", item["cta"], "--music", item["music"], "--voice", item["voice"],
            "--keypad-code", "2252960", "--output", str(final),
        ]
        completed = subprocess.run(command, cwd=v7, text=True, capture_output=True, timeout=900, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"v7 compose-video failed for {task_id}: {(completed.stderr or completed.stdout)[-900:]}")

        first_frame = qa_dir / f"{task_id}_first-frame.png"
        moving_frame = qa_dir / f"{task_id}_moving-frame.png"
        _extract_frame(final, 0, first_frame)
        _extract_frame(final, 2.0, moving_frame)
        hook_probe, final_probe = _probe(hook), _probe(final)
        first_rms = _rms_difference(master, first_frame)
        cover_rms = _rms_difference(master, cover)
        first_cover_rms = _rms_difference(cover, first_frame)
        motion_rms = _rms_difference(first_frame, moving_frame)
        poster_region_rms = _poster_region_rms(master, moving_frame)
        combination = (item["interface_operation"], Path(item["cta"]).name, Path(item["music"]).name, tuple(Path(path).name for path in item["middle_segments"]))
        duplicate = combination in combinations
        combinations.add(combination)
        qa = {
            "task_id": task_id,
            "master_1080x1920": Image.open(master).size == (W, H),
            "poster_fully_visible": item["master_layout"]["cropped"] is False and item["master_layout"]["poster_box"] == [0, 285, 1080, 1635],
            "hook_probe": hook_probe,
            "hook_3_seconds": abs(hook_probe["duration"] - 3.0) <= 0.08,
            "final_probe": final_probe,
            "final_12_seconds": abs(final_probe["duration"] - 12.0) <= 0.08,
            "final_1080x1920": (final_probe["width"], final_probe["height"]) == (W, H),
            "first_frame_rms_vs_master": round(first_rms, 3),
            "first_frame_faithful": first_rms < 18.0,
            "cover_rms_vs_poster_master": round(cover_rms, 3),
            "cover_is_complete_poster_master": cover_rms < 5.0,
            "first_frame_rms_vs_cover": round(first_cover_rms, 3),
            "first_frame_matches_video_cover": first_cover_rms < 18.0,
            "first_frame_and_cover_source": "complete uncropped poster master",
            "hook_motion_rms": round(motion_rms, 3),
            "hook_is_dynamic": motion_rms > 1.0,
            "poster_region_rms_at_2s": round(poster_region_rms, 3),
            "poster_facts_stable_at_2s": poster_region_rms < 18.0,
            "component_combination_duplicate": duplicate,
            "no_duplicate_combination": not duplicate,
        }
        qa["passed"] = all(
            qa[key] for key in (
                "master_1080x1920", "poster_fully_visible", "hook_3_seconds", "final_12_seconds",
                "final_1080x1920", "first_frame_faithful", "cover_is_complete_poster_master",
                "first_frame_matches_video_cover", "hook_is_dynamic", "no_duplicate_combination",
                "poster_facts_stable_at_2s",
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
        _write_json(phase4_dir / "build-manifest.json", {"schema_version": "jaguartv-v7-postmatch-build-v1", "target_date": target_date.isoformat(), "updated_at": utc_now(), "voice_rotation": voice_rotation_meta, "items": manifest_items})

    captions_path = phase4_dir / "captions.json"
    _write_json(captions_path, _captions(entries, research_by_id, target_date))
    return {
        "ok": True,
        "target_date": target_date.isoformat(),
        "video_count": len(manifest_items),
        "build_manifest": str((phase4_dir / "build-manifest.json").resolve()),
        "captions": str(captions_path.resolve()),
        "validations": validations,
    }
