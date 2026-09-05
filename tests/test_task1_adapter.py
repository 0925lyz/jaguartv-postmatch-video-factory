from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from jaguartv_postmatch.task1 import (
    _load_from_prematch_runs,
    _normalize_kickoff,
    _resolve_crest,
    load_task1_fixtures,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


class Task1InputAdapterTests(unittest.TestCase):
    def test_resolve_crest_normalizes_accents_and_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image2_database = Path(directory)
            _touch(image2_database / "assets" / "crests" / "260902" / "Atletico_MG.png")
            _touch(image2_database / "assets" / "crests" / "260830" / "Internacional.png")
            self.assertIn("Atletico_MG.png", _resolve_crest(image2_database, "Atlético-MG"))
            self.assertIn("Internacional.png", _resolve_crest(image2_database, "Internacional"))
            # A team with no crest anywhere resolves to empty, never a false match.
            self.assertEqual(_resolve_crest(image2_database, "Náutico"), "")

    def test_resolve_crest_prefers_newest_collection_date(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image2_database = Path(directory)
            old = _touch(image2_database / "assets" / "crests" / "260830" / "Santos.png")
            new = _touch(image2_database / "assets" / "crests" / "260902" / "Santos.png")
            self.assertEqual(_resolve_crest(image2_database, "Santos"), str(new.resolve()))
            self.assertNotEqual(_resolve_crest(image2_database, "Santos"), str(old.resolve()))

    def test_normalize_kickoff_accepts_colon_and_h_formats(self) -> None:
        self.assertEqual(_normalize_kickoff("18:30"), "18:30")
        self.assertEqual(_normalize_kickoff("8:05"), "08:05")
        self.assertEqual(_normalize_kickoff("18H30"), "18:30")
        self.assertEqual(_normalize_kickoff(""), "")

    def test_load_from_prematch_runs_prefers_latest_batch_and_resolves_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prematch_root = root / "prematch"
            image2_database = root / "image2"

            for batch in ("batch1", "batch2"):
                run_dir = prematch_root / "runs" / f"20260904_{batch}"
                _write_json(
                    run_dir / "phase1" / "selected-fixtures.json",
                    {
                        "date": "2026-09-04",
                        "fixtures": [
                            {
                                "fixture_id": "manual-20260904-santos-internacional",
                                "competition": "Brasileirão Série A",
                                "home_team": "Santos",
                                "away_team": "Internacional",
                                "schedule_date": "2026-09-04",
                                "kickoff_at_brt": "18:30",
                                "channels": ["Premiere"],
                            }
                        ],
                    },
                )
                poster = _touch(
                    run_dir
                    / "phase3"
                    / "posters"
                    / f"桑托斯_vs_巴西国际_260904_海报_{batch}.png"
                )
                _write_json(
                    run_dir / "phase3" / "poster-manifest.json",
                    {
                        "ok": True,
                        "status": "PHASE3_COMPLETE",
                        "items": [
                            {
                                "task_id": "manual-20260904-santos-internacional",
                                "kind": "single",
                                "poster": str(poster),
                            },
                            {
                                "task_id": "schedule-260904",
                                "kind": "schedule",
                                "poster": str(_touch(run_dir / "phase3" / "posters" / "schedule.png")),
                            },
                        ],
                    },
                )

            _touch(image2_database / "assets" / "crests" / "260902" / "Santos.png")
            _touch(image2_database / "assets" / "crests" / "260830" / "Internacional.png")

            fixtures = _load_from_prematch_runs(prematch_root, image2_database, date(2026, 9, 4))

            self.assertEqual(len(fixtures), 1)
            fixture = fixtures[0]
            self.assertEqual(fixture.task1_fixture_id, "manual-20260904-santos-internacional")
            self.assertEqual(fixture.home_team, "Santos")
            self.assertEqual(fixture.away_team, "Internacional")
            self.assertEqual(fixture.kickoff_brt, "18:30")
            self.assertEqual(fixture.channels, ["PREMIERE"])
            # The later batch's poster must win.
            self.assertIn("batch2", fixture.pre_match_poster)
            self.assertTrue(Path(fixture.home_crest).is_file())
            self.assertTrue(Path(fixture.away_crest).is_file())

    def test_load_from_prematch_runs_skips_incomplete_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prematch_root = root / "prematch"
            image2_database = root / "image2"

            run_dir = prematch_root / "runs" / "20260904_batch1"
            _write_json(
                run_dir / "phase1" / "selected-fixtures.json",
                {
                    "fixtures": [
                        {
                            "fixture_id": "manual-20260904-santos-internacional",
                            "competition": "Brasileirão Série A",
                            "home_team": "Santos",
                            "away_team": "Internacional",
                            "schedule_date": "2026-09-04",
                            "kickoff_at_brt": "18:30",
                            "channels": ["Premiere"],
                        }
                    ],
                },
            )
            # No poster manifest and no crests -> the fixture is unusable.
            self.assertEqual(
                _load_from_prematch_runs(prematch_root, image2_database, date(2026, 9, 4)),
                [],
            )

    def test_load_from_prematch_runs_caches_authorized_missing_crests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prematch_root = root / "prematch"
            image2_database = root / "image2"
            run_dir = prematch_root / "runs" / "20260905_batch1"
            poster = _touch(run_dir / "phase3" / "posters" / "poster.png")
            _write_json(
                run_dir / "phase1" / "selected-fixtures.json",
                {
                    "fixtures": [
                        {
                            "fixture_id": "manual-20260905-man-city-inter",
                            "competition": "Club World Cup",
                            "home_team": "Man City",
                            "away_team": "Inter",
                            "schedule_date": "2026-09-05",
                            "kickoff_at_brt": "20:00",
                            "channels": ["ESPN"],
                        }
                    ],
                },
            )
            _write_json(
                run_dir / "phase3" / "poster-manifest.json",
                {"items": [{"task_id": "manual-20260905-man-city-inter", "kind": "single", "poster": str(poster)}]},
            )

            def fake_download(_url: str, team: str, database: Path, yymmdd: str) -> str:
                path = database / "assets" / "crests" / yymmdd / f"{team}.png"
                return str(_touch(path).resolve())

            with patch(
                "jaguartv_postmatch.task1._api_football_logos",
                return_value={("manchester city", "inter"): ("https://example.test/city.png", "https://example.test/inter.png")},
            ), patch("jaguartv_postmatch.task1._download_crest", side_effect=fake_download):
                fixtures = _load_from_prematch_runs(prematch_root, image2_database, date(2026, 9, 5))

            self.assertEqual(len(fixtures), 1)
            self.assertTrue(Path(fixtures[0].home_crest).is_file())
            self.assertTrue(Path(fixtures[0].away_crest).is_file())

    def test_load_task1_fixtures_falls_back_to_legacy_when_no_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # A prematch root with no matching runs, and a legacy image2 db.
            prematch_root = root / "prematch"
            (prematch_root / "runs").mkdir(parents=True)
            image2_database = root / "image2"

            home_crest = _touch(image2_database / "assets" / "crests" / "260902" / "Santos.png")
            away_crest = _touch(
                image2_database / "assets" / "crests" / "260902" / "Palmeiras.png"
            )
            poster = _touch(root / "赛前海报" / "桑托斯_vs_帕尔梅拉斯_260902_海报.png")
            production_dir = image2_database / "outputs" / "260902_prematch_image2"
            _write_json(
                production_dir / "production_manifest_santos_palmeiras_260902.json",
                {
                    "match": {
                        "match_id": "santos_palmeiras_260902",
                        "competition": "COPA DO BRASIL • QUARTAS DE FINAL",
                        "home": "SANTOS",
                        "away": "PALMEIRAS",
                        "brasilia_date": "02 SET 2026",
                        "brasilia_time": "21H30",
                        "channels": "GLOBO • SPORTV • PREMIERE",
                    },
                    "assets": {
                        "home_crest": str(home_crest),
                        "away_crest": str(away_crest),
                    },
                    "generation": {"final_poster": str(poster)},
                },
            )

            fixtures = load_task1_fixtures(image2_database, date(2026, 9, 2), prematch_root)
            self.assertEqual(len(fixtures), 1)
            self.assertEqual(fixtures[0].task1_fixture_id, "santos_palmeiras_260902")
            self.assertEqual(fixtures[0].kickoff_brt, "21:30")
            self.assertEqual(fixtures[0].channels, ["GLOBO", "SPORTV", "PREMIERE"])

    def test_load_task1_fixtures_raises_when_both_sources_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prematch_root = root / "prematch"
            (prematch_root / "runs").mkdir(parents=True)
            image2_database = root / "image2"
            with self.assertRaises(FileNotFoundError):
                load_task1_fixtures(image2_database, date(2026, 9, 4), prematch_root)


if __name__ == "__main__":
    unittest.main()
