from __future__ import annotations

import json
from pathlib import Path

import pytest

from jaguartv_postmatch.pipeline import load_config, preflight
from jaguartv_postmatch.util import codex_model_args, executable_path


def test_config_rejects_wrong_server_label(tmp_path: Path) -> None:
    config = {
        "collector_root": ".",
        "system_prompts_and_models": ".",
        "jaguartv_v7_pack": ".",
        "image2_database": ".",
        "figure_1": ".",
        "media_assets_root": ".",
        "channel_icons": ".",
        "pending_review_uploader": ".",
        "runtime_root": "runtime",
        "reasoning": {
            "primary_model": "current-task",
            "fallback_model": "current-task",
        },
        "server": {"category": "post_match_score", "label": "赛前预测"},
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="赛后比分"):
        load_config(path)


def test_preflight_reports_active_api_primary_and_apimart_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        key: str(tmp_path)
        for key in (
            "collector_root", "system_prompts_and_models", "jaguartv_v7_pack",
            "image2_database", "figure_1", "media_assets_root", "channel_icons", "pending_review_uploader",
            "runtime_root",
        )
    }
    config["result_sources"] = {"api_football_required": False}
    monkeypatch.setattr("jaguartv_postmatch.pipeline._credential_available", lambda name: name == "APIMART_API_KEY")
    monkeypatch.setattr("jaguartv_postmatch.pipeline._research_connectivity", lambda: {})
    report = preflight(config)
    assert report["checks"]["image2_primary"]["provider"] == "active-large-model-api"
    assert report["checks"]["image2_fallback"]["provider"] == "apimart"
    assert report["checks"]["image2_route"]["available"] is True


def test_preflight_reports_dreamina_primary_and_apimart_video_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        key: str(tmp_path)
        for key in (
            "collector_root", "system_prompts_and_models", "jaguartv_v7_pack",
            "image2_database", "figure_1", "media_assets_root", "channel_icons", "pending_review_uploader",
            "runtime_root",
        )
    }
    config["video"] = {}
    config["result_sources"] = {"api_football_required": False}
    monkeypatch.setattr("jaguartv_postmatch.pipeline._credential_available", lambda name: name == "APIMART_API_KEY")
    monkeypatch.setattr("jaguartv_postmatch.pipeline._research_connectivity", lambda: {})
    monkeypatch.setattr("jaguartv_postmatch.pipeline.shutil.which", lambda name: None if name == "dreamina" else "/bin/true")
    report = preflight(config)
    assert report["checks"]["video_primary"]["provider"] == "dreamina-vip"
    assert report["checks"]["video_fallback"]["model"] == "wan2.6-i2v-flash"
    assert report["checks"]["video_route"]["available"] is True


def test_preflight_allows_research_when_x_is_down_but_exa_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        key: str(tmp_path)
        for key in (
            "collector_root", "system_prompts_and_models", "jaguartv_v7_pack",
            "image2_database", "figure_1", "media_assets_root", "channel_icons", "pending_review_uploader",
            "runtime_root",
        )
    }
    config["result_sources"] = {"api_football_required": False}
    monkeypatch.setattr("jaguartv_postmatch.pipeline._credential_available", lambda _name: True)
    monkeypatch.setattr(
        "jaguartv_postmatch.pipeline._research_connectivity",
        lambda: {
            "agent_reach_exa": {"available": True},
            "agent_reach_x": {"available": False},
        },
    )
    report = preflight(config)
    assert report["checks"]["research_route"]["available"] is True
    assert "agent_reach_x" in report["optional_unavailable"]
    assert "agent_reach_x" not in report["required_missing"]


def test_executable_path_falls_back_to_local_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_bin = tmp_path / ".local" / "bin"
    local_bin.mkdir(parents=True)
    tool = local_bin / "mcporter"
    tool.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("jaguartv_postmatch.util.shutil.which", lambda _name: None)
    monkeypatch.setattr("jaguartv_postmatch.util.Path.home", lambda: tmp_path)
    assert executable_path("mcporter") == str(tool)


def test_codex_model_args_inherits_current_task_by_default() -> None:
    assert codex_model_args("current-task") == []
    assert codex_model_args("hy4") == ["-m", "hy4"]
    assert codex_model_args("gpt-5.1") == ["-m", "gpt-5.1"]
    assert codex_model_args("deepseek-v4-flash", "deepseek") == [
        "-c",
        'model_provider="deepseek"',
        "-m",
        "deepseek-v4-flash",
    ]
