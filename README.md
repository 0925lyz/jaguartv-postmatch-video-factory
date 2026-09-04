# JaguarTV 任务3赛后比分视频工厂

这是从 Original Content Factory 抽离的独立任务3赛后生产仓库。它只消费任务1已选赛程，完成
赛果核验、赛后研究、Image2 海报、即梦/Seedance 短视频、素材轮循和服务器待审核上传。
它不创建新的比赛筛选清单，也不修改 `Video-creation-automation`。

Canonical local path: `/Users/jaguar/WorkBuddy/赛前/jaguartv-postmatch-video-factory`
GitHub: `0925lyz/jaguartv-postmatch-video-factory`

## 流程

1. 从任务1清单读取稳定比赛 ID、开赛时间和全部频道。
2. 每天 01:00 Brasília 从 API-Football 拉取赛后比分，只对任务1已选赛程做匹配；`copa.jarg.top` 保留为频道/原始页面交叉核验，仅接收 `FT`、`AET`、`PEN`。
3. 使用 agent-reach 收集当前比赛的可追溯赛后证据和参赛球员信息。
4. 通过 Image2 生成 4:5 巴葡赛后海报，Figure 1 固定右上，比分置于脸部安全区下方。
5. 只用即梦 VIP / Seedance 生成前 3-4 秒海报动态钩子。
6. 钩子只让海报背景和比分情绪动起来：赢家可激烈跳跃庆祝，输家可捶草坪、叹气、埋头或抱怨；右上 Figure 1 原图 logo、比分、队徽和日期不能变形或漂移。
7. 后续操作片段、CTA、音乐和口播全部从已有授权库存轮循拼接，由 V7 组装为 12 秒中文命名成片；文件名前缀按 `01`、`02`、`03` 排序。
8. 首帧和封面完整复现海报。
9. 幂等上传到 Pending Review 的 `赛后比分` 标签，不自动发布；桌面交付目录为 `每日赛后海报` 和 `赛后比分`。

海报风格优先保留最新 WorkBuddy 赛后批次已认可的构造：真人球星、强景深、干净戏剧背景、大比分层级、明确胜负情绪。找不到可验证真人信息时，使用匿名虚拟硬汉球员；项目内球员、队徽和球衣素材按操作员授权处理。
队徽会优先复用 `image2数据库/assets/crests` 全库；任务一 manifest 路径失效或当天目录缺失时，会自动缓存官方来源队徽到当天目录，不再因授权占位要求跳过比赛。

海报背景生成方法与贴字注意事项见 [docs/poster-master-prompt.md](docs/poster-master-prompt.md) 与 [docs/poster-production-rules.md](docs/poster-production-rules.md)。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config/postmatch.example.json config/local.json
```

`config/local.json` 只保存本机路径，不保存密钥。密钥通过环境变量或 macOS Keychain 提供。
仓库不会追踪运行数据库、生成媒体、浏览器会话或上传凭据。
API-Football 密钥读取 `API_FOOTBALL_KEY`，不要写进配置或仓库。
文字提示词默认使用当前任务窗口正在执行的大模型；也可以在 `reasoning.primary_model`
里指定 `hy3`、`hy4`、`deepseek` 或 `gpt` 系列等已配置模型。

## 使用

```bash
jaguartv-postmatch preflight --config config/local.json
jaguartv-postmatch phase1 --config config/local.json --date 2026-08-31
jaguartv-postmatch run --config config/local.json --date 2026-08-31
```

每日入口：

```bash
scripts/run-daily.sh
```

素材交付工具可将历史成片、赛前/赛后海报、4-9 秒操作段和末 3 秒 CTA 按 SHA-256 去重：

```bash
jaguartv-media-library --config config/media-library.example.json
```

更多边界和失败策略见 [docs/architecture.md](docs/architecture.md)。
