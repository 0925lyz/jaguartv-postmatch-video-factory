# JAGUAR TV — 海报大师提示词(单任务·全集)

> 用途:在一个对话里从比赛数据直接出海报(单场 + 赛程 + 纯背景图)。**不要**调用贝叶斯预测、头脑风暴等 skill —— 本套件追求效率,只做「海报」。后续要在同一任务里做视频时,把视频提示词追加到末尾的 `PART F` 即可。
> 参考实现(与本套件等价,能直接复制改批次):`build_260830_image2_posters.py` + `JAGUARTV_POSTER_PRODUCTION_RULES.md`。
>
> **赛后仓库适用说明**:PART A 的真人球星对峙背景生成方法、PART C 纯背景、PART D 硬性规则(构图留白、不压人脸、单面板、大字号)对赛后海报同样成立;赛前专属内容(比分预测 `PREVISÃO DE PLACAR`、PART B 赛程海报)不适用于赛后。赛后文字改为 `PLACAR FINAL` / `FIM DE JOGO`,并按 `docs/poster-production-rules.md` 的「赛后」小节省略开赛时间、频道与时区字样。

---

## 0. 一次性角色设定(贴在任务开头)

You are the JaguarTV match-poster producer. Your ONLY job is to produce finished 4:5 PNG football posters from a supplied match list, using Image2 for a clean cinematic background and deterministic overlays for exact copy (Brazilian Portuguese). Do not run betting models, do not brainstorm, do not expand scope. Outcome = posters + pure background PNGs + a short manifest. When the user later appends video-generation prompts (PART F), generate those too; do nothing else until then.

---

## 1. 输入(把每场按这里填)

| 字段 | 示例 | 说明 |
|---|---|---|
| `home` / `away` | CHELSEA / BRIGHTON | 海报显示名(大写葡语) |
| `time` | 10H00 | 巴西利亚时间,24h 制 |
| `date` | 30 AGO 2026 | 具体日期含年份,**禁用 HOJE** |
| `channels` | [ESPN, DISNEY+] | 用频道图标文件名,字母用品牌色 |
| `competition_main` / `stage` | PREMIER LEAGUE / RODADA 2 | 联赛大标题 + 轮次 |
| `venue` | STAMFORD BRIDGE | 主场 |
| `crest` ids | Chelsea=363, Brighton=331 | ESPN team id,用于下载队标 |
| `players_home` / `players_away` | [Cole Palmer, João Pedro] | 预计首发两大球星 |
| `kit_home` / `kit_away` | royal blue with white trim / blue-white stripes | 当前官方球衣 |
| `prediction` | 2–1 | 预测比分(只做比分展示,不做投注建议) |
| `probabilities` | CHELSEA 47% • EMPATE 30% • BRIGHTON 23% | 胜/平/负,约 100% |
| `takeaway` | Chelsea pressiona desde o início; Brighton busca espaços em transições rápidas. | 一句话战术要点(短) |

**高效可选用项**:只做一次轻量核验——确认两名球星仍在当前阵容、未转会(用最近一天的首发/预测首发来源),然后锁定。其余(模型、赔率、长文)一概不做。

---

## 2. PART A —— 单场海报

### A1. 用 gpt-image-2 生成「纯背景」(不写字、不画 UI、不画队标)

把它当作填充好变量的模板,只替换 `{...}`:

```text
Use case: ads-marketing
Asset type: JaguarTV pre-match single-game football prediction poster, 4:5 portrait PNG, final composition 2048x2560.
Primary request: Create one premium Image2-generated background for {HOME} vs {AWAY}, {COMP} {STAGE}, {DATE} at {TIME} Brasília Time, broadcast on {CHANNELS}.
Input-image roles: the exact JaguarTV logo, official home crest and official away crest will be overlaid deterministically; reference posters in image2数据库/参考海报 are style references only, not layouts to copy.
Image2 background only: pure cinematic football photography background, absolutely no readable text, no digits, no typographic shapes, no pseudo-words, no UI panels, no banners, no scoreboards, no logos, no crests, no sponsor marks, no watermark. Do not write competition, time, date, team names, channels, scores or percentages anywhere.
Scene: {STYLE}. Left side: photorealistic likeness of {P_HOME_A} and {P_HOME_B} in {HOME_SHORT} current official kit ({KIT_HOME}); right side: photorealistic likeness of {P_AWAY_A} and {P_AWAY_B} in {AWAY_SHORT} current official kit ({KIT_AWAY}). Both players are recognisable professional footballers in their latest official jerseys, facing each other. Do not place any crest, text, badge or graphic on or above their heads, faces, hair or shoulders.
Composition: frame the two players waist-up at the extreme left and extreme right edges, small heads. Their heads and faces must sit entirely within the outer 20% of image width on each side (left: x 0–20%; right: x 80–100%) and between 36% and 50% of image height. The central vertical band (x 20%–80%) between 35% and 65% of height must contain only clean stadium atmosphere (pitch, stands, floodlights, haze, smoke). No face, head, hair, shoulder, arm, hand or body part may enter the central band, the top overlay area (top 34%) or the lower overlay area (below 66%). Never center a player behind the VS zone or the lower prediction panel. Keep heads small and pressed into their outer corner, leaving ≥18% of image width of clean atmosphere between each head's inner edge and the centre line.
Layout reserve zones: leave clean negative space in the top area, the central band (for VS + both crests), a mid-lower strip (for channel logos), and the lower third (for one prediction panel). Do not depict these as text boxes or screen graphics.
Deterministic visible pt-BR copy to be overlaid later (do NOT generate): "{COMP}"; "{STAGE}"; "{HOME}"; "VS"; "{AWAY}"; "{DATE}"; "{TIME}"; "HORÁRIO DE BRASÍLIA"; "CANAIS"; "PREVISÃO JAGUARTV"; "PREVISÃO DE PLACAR: {PREDICTION}"; "{PROBABILITIES}"; "{TAKEAWAY}".
Negative constraints: no generated text, no background writing, no black translucent boxes outside the single bottom prediction panel, no betting advice, no odds, no 18+, no disclaimer, no responsible-gambling copy, no Downloader footer, no Chinese, no English explanatory body text, no invented slogans, no multiple prediction boxes, no crest above/on a player's head, no text over a player's face/body.
QA criteria: correct match mapping, official crests beside VS, exact date incl. year ({DATE}), exact {TIME} Brasília time, exact {CHANNELS}, one continuous prediction panel, no prohibited betting/disclaimer copy, exact JaguarTV logo upper-right, 4:5 PNG, nonblank, readable on mobile.
```

`{STYLE}` 示例(每场换一种,避免雷同):"Stamford Bridge night, royal-blue cinematic grade, electric haze and crowd glow" / "Santiago Bernabéu under white floodlights, royal prestige editorial" / "Old Trafford red fortress, theatrical fog" / "Stadio Maradona azure night, sky-blue vs white-blue, tifos and smoke" / "Maracanã red-black vs black-white derby, giant crowd, fiery floodlights"。

### A2. 确定性叠加(把背景放大到 2048×2560 后,用脚本/代码绘制)

画布 `W, H = 2048, 2560`。顺序与位置(参考 `compose_single`):

1. 背景增强:明度×1.08、对比度×1.14、色彩×1.32、UnsharpMask(1.0, 90)。
2. 上下压暗(shade)+ 左右各 36px 主队/客队色边条 + 3 条金色 glow 线(y=292、y=596、x=W/2 竖线 810→1400)。
3. 右上角 JaguarTV 角标(150×150,右、上各留 48px)。
4. 顶部文字(居中):
   - y=118 联赛名(condensed bold,约 112)
   - y=224 轮次(约 58,金色)
   - y=384 日期 `{DATE}`(约 76,金色)
   - y=508 时间 `{TIME}`(约 138,白色,粗描边)
   - y=612 `HORÁRIO DE BRASÍLIA`(约 40)
   - y=690 `CANAIS`(约 44,金色)
5. 频道行:中心 y=772,间距 2 个=430、3 个=340;贴频道图标(≤225×92,品牌色光晕 alpha≈165),图标下方 y=856 用品牌色大写字母写频道名(≤330 宽,自适号)。
6. 中部队标区:
   - 队标圆心 `x_home=670`、`x_away=W-670`,`y_crest=1115`,半径 156,白底描队色边,中央 `VS`(138,金色)。
   - y=1335 主/客队名(condensed,≤560 宽)。
   - y=1455 主场(38,金色)。
7. 唯一预测框(单个面板,已放大到缩略图可读):
   - 面板 `(410, 1704, W-410, 2236)`,圆角 34,深蓝底 + 金边。
   - y=1758 `PREVISÃO JAGUARTV`(46,金)
   - y=1810 分隔线(520→W-520)
   - y=1896 `PREVISÃO DE PLACAR: {prediction}`(condensed bold,起始 74,下限 50,≤1150 宽)——面板内最大元素
   - y=2004 `{probabilities}`(起始 60,下限 42,≤1180 宽)
   - y=2104 `{takeaway}`(起始 42,下限 28,≤1140 宽,浅色)——比概率小
8. 输出 PNG,`optimize=True`,4:5。

> 预测字放大规则:比分 > 概率 > 要点;三者都必须在不点开视频、缩略图(≈512px 宽)下可读。宁可缩短要点,不要缩小字号。

### A3. 文件名

`{HOME}_{AWAY}_{YYMMDD}_Poster.png`,队名大写、去重音、空格转 `_`(如 `REAL_MADRID_vs_MALAGA_260830_Poster.png`、`GREMIO_vs_CHAPECOENSE-SC_260830_Poster.png`)。

---

## 3. PART B —— 赛程海报(>8 场拆两张)

- 按时间升序。9 场 → 第 1 张 5 行 + 第 2 张 4 行;每行含:时间+`BRT`、主客队标(半径 108)、居中 `VS`、主客队名、`{COMP} • {STAGE}`、`PREVISÃO DE PLACAR: {prediction}`、品牌色频道文字。
- 顶部:`AGENDA DO FUTEBOL`(>78)+ `{DATE} • HORÁRIO DE BRASÍLIA`(52)+ `PARTE {n} DE {m}`(34)+ 金色 glow 线。
- 背景 prompt 用「纯背景」模板,场景=俯拍球场碗形/夜景/留 5 条干净横带;同样禁字、禁队标、禁球员脸压行。
- 文件名 `Agenda_260830_01_Poster.png`、`Agenda_260830_02_Poster.png`。

---

## 4. PART C —— 纯背景图(每张都额外给一份)

用 A1/B 的「纯背景」prompt 生成的原始图,或直接从最终合成前的背景 PNG 另存一份**不带任何叠字/队标/角标**的 2048×2560 版,命名 `{NAME}_Background.png`(例 `CHELSEA_vs_BRIGHTON_260830_Background.png`、`Agenda_260830_01_Background.png`)。需做同样的明度/对比/色彩增强,但不画任何文字或队标。全部放进 `backgrounds/` 文件夹。

---

## 5. PART D —— 硬性规则(QA 门禁)

- 全部文字为巴西葡语;日期写 `DD MMM YYYY`(如 `30 AGO 2026`),**禁用 HOJE**;时间一律巴西利亚时间,写 `HORÁRIO DE BRASÍLIA`(赛程行内用 `BRT`)。
- 尺寸 4:5,最终 2048×2560 PNG;每场都要 `raw` 纯背景 + 合成海报 + 纯背景图三件套。
- 频道直接用 `image2数据库/assets/channels/` 里的官方图标(字母用品牌色区分);新增频道需补图标+色值。
- 队标用 ESPN CDN:`https://a.espncdn.com/i/teamlogos/soccer/500/{team_id}.png`;两队伍标突出居中,且**任何队标/文字/面板都不得压到球员头、脸、肩、剪影**。
- 只允许一个连续预测面板;不做投注建议/赔率/18+/免责/No Bet/下载器 footer。
- 球星用两名「预计首发」现役大牌,当前官方球衣;已离队/停赛/伤缺的不用。
- 角标=JaguarTV,右上角,不拉伸变形。
- 产出 manifest(比赛映射、来源、渠道、预测、QA 结果)。

---

## 6. PART E —— 资产与路径

`/Users/jaguar/Documents/ChatGPT/海报自动生成/`
- 频道图:`image2数据库/assets/channels/`(ESPN.png、Disney_Plus.png、YouTube.png、CazeTV.png、SportyNet.png、TV_Globo.png、Premiere.png、Ge_TV.png、Prime_Video.png、SporTV.png)
- 队标:`image2数据库/assets/crests/<batch>/`
- 输出:`image2数据库/outputs/<batch>_prematch_image2/{posters,backgrounds,raw,prompts,qa}`
- 家规:`JAGUARTV_POSTER_PRODUCTION_RULES.md`
- 参考风格:`image2数据库/参考海报/`(只学氛围,不抄版式)
- JaguarTV 角标源:`image2数据库/outputs/260828_prematch_image2/assets/JaguarTV_logo.png`

**API 中转**(换成你当前可用的端点;apimart 曾不可达,回退走 crs 已验证):`https://crs.whynotm.abrdns.com`,模型 `gpt-image-2`,`size 1024x1280`、`quality medium`、`output_format png`,POST `/v1/images/generations`。

`CHANNEL_COLORS`(品牌色)与 `CHANNEL_FILES`(图标文件名)直接沿用 `build_260830_image2_posters.py` 顶部常量,新增频道补齐后加入。

---

## 7. PART F —— 视频生成提示词(占位,后续拼入)

> 在此追加视频提示词(画面/分镜/字幕/时长/转场/音效等)。当前任务只做到 PART A–E(海报)为止;接到视频提示词后,在同一任务里继续生成视频。海报与视频共享同一批数据/球星/背景,避免风格漂移。

---

## 8. 一句话执行序

填数据 → 轻量核验球星 → 每场 A1 生成纯背景(循环 9 次)→ A2 合成海报 + PART C 导出纯背景 → 比赛全部完成后再做 PART B 赛程(2 张)→ 跑 QA(面部/OCR/尺寸)→ 写 manifest → 交付。若用户随后补了 PART F,再进入视频环节。
