from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from .credentials import CredentialUnavailable, env_or_keychain
from .util import utc_now


DEFAULT_TEXT = "Bora pro jogo! Sete dias grátis no Jaguar TV."
DEFAULT_SPEED = "1.30"
DEFAULT_INVENTORY = [
    {
        "id": "nova-01",
        "voice": "nova",
        "text": "Bora pro jogo! Sete dias grátis no Jaguar TV.",
    },
    {
        "id": "shimmer-01",
        "voice": "shimmer",
        "text": "Baixe agora! Jaguar Tê Vê, sete dias grátis!",
    },
    {
        "id": "onyx-01",
        "voice": "onyx",
        "text": "Vem com tudo! Futebol ao vivo é no Jaguar TV!",
    },
    {
        "id": "onyx-02",
        "voice": "onyx",
        "text": "Não perde tempo! Baixe agora o app Jaguar TV!",
    },
]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return cleaned or "cta"


def cta_voice_filename(voice_name: str, variant_id: str) -> str:
    return f"cta-voice-ptbr-{_safe_name(voice_name)}-{_safe_name(variant_id)}.wav"


def _voice_script(config: dict[str, Any], v7: Path) -> Path:
    configured = (config.get("voiceover") or {}).get("script") or v7 / "scripts" / "generate-apimart-tts.mjs"
    return Path(str(configured)).expanduser().resolve()


def _apimart_base_url() -> str:
    return str(os.environ.get("APIMART_BASE_URL") or "https://api.apimart.ai/v1").strip().rstrip("/")


def _sanitized_error(message: str) -> str:
    text = re.sub(r"sk-[A-Za-z0-9_*.-]+", "sk-[redacted]", str(message))
    text = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer [redacted]", text)
    return text[-600:]


def generate_apimart_voice(
    config: dict[str, Any],
    v7: Path,
    voice_name: str,
    variant_id: str,
    *,
    text: str = DEFAULT_TEXT,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    voiceover_dir = (
        output_dir
        or v7 / "video_templates" / "jaguartv_match_video_factory" / "assets" / "replaceable" / "voiceover"
    ).resolve()
    voiceover_dir.mkdir(parents=True, exist_ok=True)
    output = voiceover_dir / cta_voice_filename(voice_name, variant_id)
    metadata = output.with_name(output.stem + ".transcript.json")
    if output.is_file() and output.stat().st_size > 1000:
        return output, metadata

    key = env_or_keychain("APIMART_API_KEY")
    script = _voice_script(config, v7)
    if not script.is_file():
        raise FileNotFoundError(f"APIMart TTS script is missing: {script}")
    voice_config = config.get("voiceover") or {}
    speed = str(voice_config.get("speed") or DEFAULT_SPEED)
    command = [
        "node",
        str(script),
        "--text",
        text,
        "--voice",
        _safe_name(voice_name),
        "--speed",
        speed,
        "--format",
        "wav",
        "--output",
        str(output),
    ]
    environment = {
        **os.environ,
        "APIMART_API_KEY": key,
        "APIMART_BASE_URL": _apimart_base_url(),
    }
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=environment,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size < 1000:
        detail = _sanitized_error(completed.stderr or completed.stdout or "APIMart TTS failed")
        raise RuntimeError(detail)

    payload = {
        "schema_version": "jaguartv-cta-voice-v1",
        "provider": "apimart",
        "model": str(voice_config.get("model") or "gpt-4o-mini-tts"),
        "voice": _safe_name(voice_name),
        "variant_id": _safe_name(variant_id),
        "language": "pt-BR",
        "text": text,
        "speed": speed,
        "generated_at": utc_now(),
        "output_path": str(output.resolve()),
    }
    metadata.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output, metadata


def prepare_voice_rotation(
    config: dict[str, Any],
    target_date: date,
    factory_root: Path,
) -> tuple[list[Path], dict[str, Any]]:
    v7 = Path(str(config["jaguartv_v7_pack"])).expanduser().resolve()
    voiceover_dir = (
        v7 / "video_templates" / "jaguartv_match_video_factory" / "assets" / "replaceable" / "voiceover"
    ).resolve()
    local_voices = sorted(path for path in voiceover_dir.glob("cta-voice-ptbr*.wav"))
    voice_config = config.get("voiceover") or {}
    inventory = voice_config.get("inventory") or DEFAULT_INVENTORY
    meta: dict[str, Any] = {
        "provider": "apimart",
        "model": str(voice_config.get("model") or "gpt-4o-mini-tts"),
        "policy": "inventory-and-local-rotation",
        "inventory_targets": inventory,
        "generated": [],
        "reused": [],
        "skipped": [],
        "fallback_to_local": bool(voice_config.get("fallback_to_local", True)),
    }

    generated: list[Path] = []
    key_available = True
    try:
        env_or_keychain("APIMART_API_KEY")
    except CredentialUnavailable:
        key_available = False

    for target in inventory:
        voice_name = str(target.get("voice") or "nova")
        variant_id = str(target.get("id") or voice_name)
        text = str(target.get("text") or DEFAULT_TEXT)
        output_path = voiceover_dir / cta_voice_filename(voice_name, variant_id)
        if output_path.is_file() and output_path.stat().st_size > 1000:
            generated.append(output_path)
            meta["reused"].append(
                {"voice": voice_name, "variant_id": variant_id, "path": str(output_path.resolve())}
            )
            continue
        if not key_available:
            meta["skipped"].append(
                {
                    "voice": voice_name,
                    "variant_id": variant_id,
                    "reason": "APIMART_API_KEY unavailable; using local voice assets",
                }
            )
            continue
        try:
            output, _ = generate_apimart_voice(
                config,
                v7,
                voice_name,
                variant_id,
                text=text,
            )
            generated.append(output)
            meta["generated"].append(
                {
                    "voice": voice_name,
                    "variant_id": variant_id,
                    "path": str(output.resolve()),
                    "generated_at": utc_now(),
                }
            )
        except Exception as error:  # noqa: BLE001
            meta["skipped"].append(
                {"voice": voice_name, "variant_id": variant_id, "reason": _sanitized_error(str(error))}
            )

    rotation = generated + [path for path in local_voices if path not in generated]
    if not rotation:
        raise FileNotFoundError("No CTA voiceover assets are available")
    return rotation, meta
