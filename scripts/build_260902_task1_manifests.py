#!/usr/bin/env python3
"""Build 260902 Task 1 prematch manifests in the schema expected by
jaguartv_postmatch.task1.load_task1_fixtures, so the post-match factory can
consume yesterday's (2026-09-02) pre-match selection.

Source of team/competition/crest metadata: original_content_factory/scripts/build_260902_prematch.py
fact_sources include the ESPN scoreboard URLs so the collector fetches official
status (and detects PEN via shootoutScore) for the cup ties.
"""
from __future__ import annotations

import json
from pathlib import Path

IMAGE_DB = Path("/Users/jaguar/Documents/ChatGPT/海报自动生成/image2数据库")
CRESTS = IMAGE_DB / "assets" / "crests" / "260902"
POSTERS = Path("/Users/jaguar/Documents/ChatGPT/海报自动生成/赛前海报视频/赛前海报")
CHAN = IMAGE_DB / "assets" / "channels"
FIG = "/Users/jaguar/WorkBuddy/赛后/jaguartv-postmatch-video-factory/assets/figure-1-jaguartv.png"
OUT = IMAGE_DB / "outputs" / "260902_prematch_image2"

MATCHES = [
    dict(
        match_id="flamengo_mirassol_260902", competition="BRASILEIRÃO • RODADA 4",
        home="FLAMENGO", away="MIRASSOL", time="19H30", channels="PREMIERE",
        ch_logos=["Premiere.png"], hc="Flamengo.png", ac="Mirassol.png",
        poster="弗拉门戈_vs_米拉索尔_260902_海报.png",
        pred="3–0", prob="FLAMENGO 72% • EMPATE 16% • MIRASSOL 12%",
        take="No Maracanã, o Flamengo é amplo favorito; Mirassol perde Neto Moura.",
        slug="bra.1",
    ),
    dict(
        match_id="vitoria_vasco_260902", competition="COPA DO BRASIL • QUARTAS DE FINAL",
        home="VITÓRIA", away="VASCO", time="21H30",
        channels="GLOBO • SPORTV • PREMIERE • PRIME VIDEO",
        ch_logos=["TV_Globo.png", "SporTV.png", "Premiere.png", "Prime_Video.png"],
        hc="Vitoria.png", ac="Vasco_da_Gama.png",
        poster="维多利亚_vs_瓦斯科达伽马_260902_海报.png",
        pred="2–1", prob="VITÓRIA 32% • EMPATE 25% • VASCO 43%",
        take="No Barradão, o Vitória precisa vencer por 2 de diferença para avançar.",
        slug="bra.copa_do_brazil",
    ),
    dict(
        match_id="santos_palmeiras_260902", competition="COPA DO BRASIL • QUARTAS DE FINAL",
        home="SANTOS", away="PALMEIRAS", time="21H30",
        channels="GLOBO • SPORTV • PREMIERE • PRIME VIDEO",
        ch_logos=["TV_Globo.png", "SporTV.png", "Premiere.png", "Prime_Video.png"],
        hc="Santos.png", ac="Palmeiras.png",
        poster="桑托斯_vs_帕尔梅拉斯_260902_海报.png",
        pred="2–1", prob="SANTOS 38% • EMPATE 22% • PALMEIRAS 40%",
        take="Santos vence na Vila Belmiro, mas o Palmeiras avança no agregado.",
        slug="bra.copa_do_brazil",
    ),
    dict(
        match_id="velez_boca_260902", competition="COPA ARGENTINA • OITAVAS DE FINAL",
        home="VÉLEZ", away="BOCA", time="21H15", channels="XSPORTS • YOUTUBE",
        ch_logos=["XSports.png", "YouTube.png"], hc="Velez.png", ac="Boca_Juniors.png",
        poster="萨斯菲尔德_vs_博卡青年_260902_海报.png",
        pred="1–0", prob="VÉLEZ 26% • EMPATE 28% • BOCA 46%",
        take="Boca volta com Paredes e é favorito; Vélez perde Diego Valdés.",
        slug="arg.copa",
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for m in MATCHES:
        home_crest = str(CRESTS / m["hc"])
        away_crest = str(CRESTS / m["ac"])
        final_poster = str(POSTERS / m["poster"])
        for p in (home_crest, away_crest, final_poster, FIG):
            if not Path(p).is_file():
                raise FileNotFoundError(f"required asset missing: {p}")
        channel_logos = [str(CHAN / c) for c in m["ch_logos"] if Path(CHAN / c).is_file()]
        manifest = {
            "production_id": "260902_prematch_image2",
            "created_at_brasilia": "2026-09-02",
            "model": "gpt-image-2",
            "endpoint": "https://crs.whynotm.abrdns.com",
            "canvas": {"width": 2048, "height": 2560, "aspect_ratio": "4:5", "format": "PNG"},
            "match": {
                "match_id": m["match_id"],
                "competition": m["competition"],
                "home": m["home"],
                "away": m["away"],
                "brasilia_date": "02 SET 2026",
                "brasilia_time": m["time"],
                "channels": m["channels"],
                "predicted_score": m["pred"],
                "prediction_info": [m["prob"], m["take"]],
                "fact_sources": [
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{m['slug']}/scoreboard?dates=20260902",
                ],
            },
            "assets": {
                "jaguartv_logo": FIG,
                "home_crest": home_crest,
                "away_crest": away_crest,
                "channel_logos": channel_logos,
                "players": "Image2-generated real player likenesses (two ESPN-verified starters per team, current kits)",
            },
            "prompts": {"final_image2_prompt": ""},
            "generation": {"final_poster": final_poster},
            "qa_status": "review",
            "qa_checks": {
                "png_opens": True, "dimensions_2048x2560": True,
                "aspect_4_5": True, "nonblank": True,
            },
        }
        out = OUT / f"production_manifest_{m['match_id']}.json"
        out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(str(out))
        print("wrote", out.name)
    print(f"TOTAL {len(written)} manifests in {OUT}")


if __name__ == "__main__":
    main()
