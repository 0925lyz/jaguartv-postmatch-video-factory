from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from jaguartv_postmatch.assets import _png_dimensions
from jaguartv_postmatch.collector import _api_football_event_result, match_api_football_event, parse_result_card_text
from jaguartv_postmatch.lock import ProcessLock
from jaguartv_postmatch.research import (
    is_match_specific_social_record,
    parse_exa_output,
)
from jaguartv_postmatch.phase3 import (
    APIMART_BASE_URL,
    CRS_BASE_URL,
    FIXED_LOGO_POLICY,
    PosterTask,
    _build_model_brief,
    generate_image2,
    match_visual_direction,
)
from jaguartv_postmatch.phase4 import (
    SINGLE_HOOK_ACTION_POLICY,
    VIDEO_ASSEMBLY_POLICY,
    _motion_context,
    video_filenames,
)
from jaguartv_postmatch.voice import cta_voice_filename
from jaguartv_postmatch.phase5 import _artifact_revision
from jaguartv_postmatch.store import WorkflowStore
from jaguartv_postmatch.task1 import Task1Fixture
from jaguartv_postmatch.task1 import load_task1_fixtures
from jaguartv_postmatch.util import single_poster_filename


def fixture() -> Task1Fixture:
    return Task1Fixture(
        task1_fixture_id="corinthians_santos_260830",
        source_fixture_id=None,
        composite_key="composite-key",
        competition="BRASILEIRÃO SÉRIE A • RODADA 25",
        home_team="CORINTHIANS",
        away_team="SANTOS",
        match_date="2026-08-30",
        kickoff_brt="16:00",
        channels=["GLOBO", "PREMIERE 2", "PREMIERE 3"],
        home_crest="/tmp/home.png",
        away_crest="/tmp/away.png",
        source_manifest="/tmp/manifest.json",
        pre_match_poster="/tmp/poster.png",
    )


def result(home_score: int, away_score: int) -> dict[str, object]:
    return {
        "task1_fixture_id": "corinthians_santos_260830",
        "result_status": "FT",
        "home_score": home_score,
        "away_score": away_score,
        "extra_time": None,
        "penalty_shootout": None,
        "official_match_date": "2026-08-30",
    }


class PostMatchWorkflowTests(unittest.TestCase):
    def test_result_card_parser_extracts_verified_score(self) -> None:
        card = parse_result_card_text(
            """Serie A • Rodada 25
GLOBO
16:00
ONTEM
Corinthians
0
x
1
Santos
ASSISTIR JOGO""",
            "CORINTHIANS",
            "SANTOS",
        )
        self.assertEqual((card.home_score, card.away_score), (0, 1))
        self.assertEqual(card.kickoff_brt, "16:00")

    def test_result_revisions_are_idempotent_and_corrections_versioned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(Path(directory) / "state.sqlite3")
            store.upsert_fixture(fixture())
            first_id, first_created = store.record_result(fixture().task1_fixture_id, result(0, 1))
            same_id, same_created = store.record_result(fixture().task1_fixture_id, result(0, 1))
            corrected_id, corrected_created = store.record_result(
                fixture().task1_fixture_id, result(1, 1)
            )
            self.assertTrue(first_created)
            self.assertFalse(same_created)
            self.assertEqual(first_id, same_id)
            self.assertTrue(corrected_created)
            self.assertNotEqual(first_id, corrected_id)
            current = store.current_results("2026-08-30")
            self.assertEqual(len(current), 1)
            self.assertEqual((current[0]["home_score"], current[0]["away_score"]), (1, 1))
            self.assertEqual(store.counts()["result_revisions"], 2)
            store.close()

    def test_daily_lock_rejects_a_second_process_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "daily.lock"
            first = ProcessLock(path)
            second = ProcessLock(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.__exit__(None, None, None)

    def test_required_chinese_filename_and_full_width_separator(self) -> None:
        name = single_poster_filename("科林蒂安", 2, 1, "帕尔梅拉斯", "260923")
        self.assertEqual(name, "科林蒂安-2：1-帕尔梅拉斯_260923_海报.png")
        self.assertNotIn(":", name)

    def test_task1_loader_reuses_authorized_crest_library_when_manifest_path_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_db = root / "image2数据库"
            production = image_db / "outputs" / "260903_prematch_image2"
            production.mkdir(parents=True)
            (image_db / "assets" / "crests" / "260901").mkdir(parents=True)
            (image_db / "assets" / "crests" / "260901" / "Nautico.png").write_bytes(b"crest")
            (production / "Botafogo-SP.png").write_bytes(b"crest")
            (production / "poster.png").write_bytes(b"poster")
            manifest = {
                "match": {
                    "match_id": "manual-20260903-nautico-botafogo-sp",
                    "competition": "SÉRIE C",
                    "home": "NÁUTICO",
                    "away": "BOTAFOGO-SP",
                    "brasilia_date": "03 SET 2026",
                    "brasilia_time": "20H00",
                    "channels": "YOUTUBE",
                },
                "assets": {
                    "home_crest": "missing/Nautico.png",
                    "away_crest": "Botafogo-SP.png",
                },
                "generation": {"final_poster": "poster.png"},
            }
            (production / "production_manifest_nautico_botafogo_sp.json").write_text(
                json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
            )
            with patch("jaguartv_postmatch.task1._api_football_logos", return_value={}):
                loaded = load_task1_fixtures(image_db, date(2026, 9, 3))
            self.assertEqual(len(loaded), 1)
            self.assertTrue(loaded[0].home_crest.endswith("Nautico.png"))
            self.assertTrue(loaded[0].away_crest.endswith("Botafogo-SP.png"))

    def test_agent_reach_exa_output_is_stored_with_provenance(self) -> None:
        records = parse_exa_output(
            """Title: Match report
URL: https://example.test/report
Published: 2026-08-30
Highlights:
Verified match-specific summary.
""",
            "2026-08-31T00:00:00+00:00",
        )
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["platform"], "web-via-agent-reach-exa")
        self.assertTrue(records[0]["use_for_facts"])

    def test_social_search_noise_is_rejected(self) -> None:
        match = {
            "home_team": "CHELSEA",
            "away_team": "BRIGHTON",
            "home_score": 4,
            "away_score": 3,
        }
        irrelevant = {
            "title": "X post by @news",
            "source_excerpt": "A passenger ferry departed on 30 August 2026.",
            "summary": "Unrelated breaking news.",
        }
        relevant = {
            "title": "X post by @sports",
            "source_excerpt": "FULL-TIME: Chelsea 4-3 Brighton.",
            "summary": "Chelsea beat Brighton 4-3.",
        }
        self.assertFalse(is_match_specific_social_record(irrelevant, match))
        self.assertTrue(is_match_specific_social_record(relevant, match))

    def test_pre_match_social_post_is_rejected(self) -> None:
        match = {
            "home_team": "CORINTHIANS",
            "away_team": "SANTOS",
            "home_score": 0,
            "away_score": 1,
        }
        record = {
            "title": "X post by @tickets",
            "source_excerpt": "Ingressos para Corinthians x Santos no domingo.",
            "summary": "Venda antecipada.",
        }
        self.assertFalse(is_match_specific_social_record(record, match))

    def test_standings_percentage_post_is_rejected(self) -> None:
        match = {
            "home_team": "CORINTHIANS",
            "away_team": "SANTOS",
            "home_score": 0,
            "away_score": 1,
        }
        record = {
            "title": "X post by @table",
            "source_excerpt": (
                "Probabilidades de titulo: Palmeiras - 60,5%; Corinthians - 0,046%; "
                "Santos - 0,001%; Vitoria - 11,3%."
            ),
            "summary": "Probabilidades da tabela apos a rodada anterior.",
        }
        self.assertFalse(is_match_specific_social_record(record, match))

    def test_png_dimension_reader_rejects_non_png(self) -> None:
        with self.assertRaises(ValueError):
            _png_dimensions(b"not-an-image")

    def test_away_winner_is_never_mapped_to_home_celebration(self) -> None:
        record = {
            "result": {
                "task1_fixture_id": "corinthians_santos_260830",
                "home_team": "CORINTHIANS",
                "away_team": "SANTOS",
                "home_score": 0,
                "away_score": 1,
            },
            "research": {"verified_match_record": {"participants": []}},
        }
        visual = match_visual_direction(record)
        self.assertEqual(visual["winner_team"], "SANTOS")
        self.assertEqual(visual["winner_side"], "right")
        self.assertEqual(visual["loser_team"], "CORINTHIANS")
        self.assertEqual(visual["loser_side"], "left")

    def test_contradictory_winner_override_is_rejected(self) -> None:
        record = {
            "result": {
                "task1_fixture_id": "corinthians_santos_260830",
                "home_team": "CORINTHIANS",
                "away_team": "SANTOS",
                "home_score": 0,
                "away_score": 1,
            },
            "research": {"verified_match_record": {"participants": []}},
        }
        with self.assertRaisesRegex(ValueError, "contradicts verified score"):
            match_visual_direction(
                record,
                {"winner_team": "CORINTHIANS", "loser_team": "SANTOS"},
            )

    def test_prompt_brief_carries_verified_goal_and_red_card_evidence(self) -> None:
        record = {
            "result": {
                "task1_fixture_id": "corinthians_santos_260830",
                "home_team": "CORINTHIANS",
                "away_team": "SANTOS",
                "home_score": 0,
                "away_score": 1,
                "competition": "BRASILEIRAO",
                "official_match_date": "2026-08-30",
                "result_status": "FT",
                "channels": ["PREMIERE"],
            },
            "research": {
                "verified_match_record": {
                    "participants": [],
                    "goals": [{"team": "SANTOS", "scorer": "Guilherme", "minute": "74'"}],
                    "incidents": {
                        "red_cards": [{"team": "CORINTHIANS", "minute": "81'", "text": "Verified dismissal"}],
                        "penalties": [],
                        "var": [],
                        "injuries": [],
                        "important_substitutions": [],
                        "serious_fouls": [],
                    },
                },
                "discussion_topics": ["Santos controlled the closing minutes."],
                "post_match_report_summaries": ["Guilherme scored the winner."],
                "source_records": [
                    {"use_for_facts": True, "summary": "Official match report confirms the red card."},
                    {"use_for_facts": False, "summary": "Unverified social claim."},
                ],
            },
        }
        match = _build_model_brief([record], date(2026, 8, 30), {})["matches"][0]
        self.assertEqual(match["verified_events"]["goals"][0]["scorer"], "Guilherme")
        self.assertEqual(match["verified_events"]["red_cards"][0]["team"], "CORINTHIANS")
        self.assertIn("verified-goalscorer-celebration", match["allowed_poster_concepts"])
        self.assertIn("verified-red-card-scene", match["allowed_poster_concepts"])
        self.assertNotIn("Unverified social claim.", match["verified_research_summaries"])

    def test_image2_uses_apimart_before_active_api(self) -> None:
        task = PosterTask("match", "single", "prompt", "poster.png", [], "style")
        with tempfile.TemporaryDirectory() as directory, patch(
            "jaguartv_postmatch.phase3.env_or_keychain", return_value="apimart-key"
        ), patch("jaguartv_postmatch.phase3._call_image_endpoint", return_value=[]) as call:
            provider, _ = generate_image2(task, Path(directory) / "raw.png", 3)
        self.assertEqual(provider, "apimart")
        self.assertEqual(call.call_args.args[0], APIMART_BASE_URL)

    def test_image2_falls_back_to_active_api_after_apimart_failure(self) -> None:
        task = PosterTask("match", "single", "prompt", "poster.png", [], "style")
        with tempfile.TemporaryDirectory() as directory, patch(
            "jaguartv_postmatch.phase3.env_or_keychain", return_value="apimart-key"
        ), patch(
            "jaguartv_postmatch.phase3._load_primary_key", return_value="active-key"
        ), patch(
            "jaguartv_postmatch.phase3._call_image_endpoint",
            side_effect=[RuntimeError("APIMart unavailable"), []],
        ) as call:
            provider, attempts = generate_image2(task, Path(directory) / "raw.png", 3)
        self.assertEqual(provider, "active-large-model-api")
        self.assertEqual([item.args[0] for item in call.call_args_list], [APIMART_BASE_URL, CRS_BASE_URL])
        self.assertEqual(attempts[0]["provider"], "apimart")
        self.assertFalse(attempts[0]["ok"])

    def test_motion_context_preserves_verified_red_card_side(self) -> None:
        entry = {
            "kind": "single",
            "results": [{
                "home_team": "CORINTHIANS",
                "away_team": "SANTOS",
                "home_score": 0,
                "away_score": 1,
                "competition": "BRASILEIRAO",
                "result_status": "FT",
            }],
        }
        research = {
            "verified_match_record": {
                "goals": [],
                "incidents": {
                    "red_cards": [{"team": "CORINTHIANS", "minute": "81'", "text": "Dismissal"}]
                },
            }
        }
        context = _motion_context(entry, research)
        self.assertIn("CORINTHIANS", context)
        self.assertIn("81'", context)
        self.assertNotIn("red cards: none", context)

    def test_server_artifact_revision_is_stable_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.txt"
            second = Path(directory) / "second.txt"
            first.write_text("poster", encoding="utf-8")
            second.write_text("video", encoding="utf-8")
            initial = _artifact_revision([first, second])
            self.assertEqual(initial, _artifact_revision([first, second]))
            second.write_text("corrected-video", encoding="utf-8")
            self.assertNotEqual(initial, _artifact_revision([first, second]))

    def test_video_files_use_the_validated_chinese_poster_name(self) -> None:
        names = video_filenames("/tmp/科林蒂安-0：1-桑托斯_260830_海报.png", 4, "01")
        self.assertEqual(names["raw_video"], "01科林蒂安-0：1-桑托斯_260830_海报_即梦动态_4秒.mp4")
        self.assertEqual(names["hook"], "01科林蒂安-0：1-桑托斯_260830_海报_动态钩子_3秒.mp4")
        self.assertEqual(names["final"], "01科林蒂安-0：1-桑托斯_260830_海报_成片_12秒.mp4")
        self.assertEqual(names["cover"], "01科林蒂安-0：1-桑托斯_260830_海报_封面_1080x1920.jpg")

    def test_api_football_final_result_maps_penalties(self) -> None:
        event = {
            "fixture": {"id": 123, "status": {"short": "PEN"}},
            "teams": {"home": {"name": "Corinthians"}, "away": {"name": "Santos"}},
            "goals": {"home": 1, "away": 1},
            "score": {"extratime": {"home": 1, "away": 1}, "penalty": {"home": 4, "away": 5}},
        }
        self.assertIs(match_api_football_event(fixture(), [event]), event)
        mapped = _api_football_event_result(event)
        self.assertTrue(mapped["completed"])
        self.assertEqual(mapped["result_status"], "PEN")
        self.assertEqual(mapped["home_score"], 1)
        self.assertEqual(mapped["penalty_shootout"], {"home": 4, "away": 5})

    def test_video_generation_scope_is_opening_hook_only(self) -> None:
        self.assertIn("opening poster hook", VIDEO_ASSEMBLY_POLICY)
        self.assertIn("later segment", VIDEO_ASSEMBLY_POLICY)
        self.assertIn("existing authorized inventory", VIDEO_ASSEMBLY_POLICY)

    def test_logo_and_hook_motion_policies_are_locked(self) -> None:
        self.assertIn("exact Figure 1 JaguarTV logo", FIXED_LOGO_POLICY)
        self.assertIn("never redesign", FIXED_LOGO_POLICY)
        self.assertIn("poster background", SINGLE_HOOK_ACTION_POLICY)
        self.assertIn("jump", SINGLE_HOOK_ACTION_POLICY)
        self.assertIn("pound the turf", SINGLE_HOOK_ACTION_POLICY)

    def test_cta_voice_filename_uses_female_voice_and_variant(self) -> None:
        self.assertEqual(
            cta_voice_filename("nova", "01"),
            "cta-voice-ptbr-nova-01.wav",
        )

    def test_cta_voice_inventory_filenames_are_stable(self) -> None:
        self.assertEqual(
            cta_voice_filename("shimmer", "02"),
            "cta-voice-ptbr-shimmer-02.wav",
        )


if __name__ == "__main__":
    unittest.main()
