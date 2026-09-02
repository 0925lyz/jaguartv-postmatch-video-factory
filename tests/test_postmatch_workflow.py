from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from jaguartv_postmatch.assets import _png_dimensions
from jaguartv_postmatch.collector import parse_result_card_text
from jaguartv_postmatch.lock import ProcessLock
from jaguartv_postmatch.research import (
    is_match_specific_social_record,
    parse_exa_output,
)
from jaguartv_postmatch.phase3 import match_visual_direction
from jaguartv_postmatch.phase4 import video_filenames
from jaguartv_postmatch.voice import cta_voice_filename
from jaguartv_postmatch.phase5 import _artifact_revision
from jaguartv_postmatch.store import WorkflowStore
from jaguartv_postmatch.task1 import Task1Fixture
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
        names = video_filenames("/tmp/科林蒂安-0：1-桑托斯_260830_海报.png", 4)
        self.assertEqual(names["raw_video"], "即梦动态-科林蒂安-0：1-桑托斯_260830_海报-4秒.mp4")
        self.assertEqual(names["hook"], "动态钩子-科林蒂安-0：1-桑托斯_260830_海报-3秒.mp4")
        self.assertEqual(names["final"], "成片-科林蒂安-0：1-桑托斯_260830_海报-12秒.mp4")
        self.assertEqual(names["cover"], "封面-科林蒂安-0：1-桑托斯_260830_海报-1080x1920.jpg")

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
