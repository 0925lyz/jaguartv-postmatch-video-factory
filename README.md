# JaguarTV 任务3赛后比分视频工厂

这是从 Original Content Factory 抽离的独立任务3赛后生产仓库。它只消费任务1已选赛程，完成
赛果核验、赛后研究、Image2 海报、即梦/Seedance 短视频、素材轮循和服务器待审核上传。
它不创建新的比赛筛选清单，也不修改 `Video-creation-automation`。

Canonical local path: `/Users/jaguar/WorkBuddy/赛前/jaguartv-postmatch-video-factory`
GitHub: `0925lyz/jaguartv-postmatch-video-factory`

## 流程

1. 从任务1清单读取稳定比赛 ID、开赛时间和全部频道。
2. 由 WorkBuddy 在其自动化中决定启动时间；仓库入口从 API-Football 拉取赛后比分，只对任务1已选赛程做匹配；`copa.jarg.top` 保留为频道/原始页面交叉核验，仅接收 `FT`、`AET`、`PEN`。
3. 使用 agent-reach 收集当前比赛的可追溯赛后证据、事件和参赛球员信息，作为海报构图素材。
4. 当前任务文本大模型先根据核实后的研究证据生成逐场英文背景提示词，再优先调用当前大模型 API 的 `gpt-image-2`；失败后才显式调用 APIMart。文字、比分、队徽、频道图标和 Figure 1 由本地确定性合成。
5. 每批只确定性选择一半海报用即梦 VIP / Seedance 2.0 Fast 720p 生成 4 秒背景动态；不可用或生成失败时，使用 APIMart `wan2.6-i2v-flash` 的 720p、4 秒后备路由。其余海报直接生成本地 4 秒静态钩子。
6. 视频模型只接收背景层；右上 Figure 1 原图 logo、文字、比分、队徽、频道图标和日期作为锁定前景逐帧合成，不能变形或漂移。
7. 后续两段操作素材和动态 CTA 全部从已有授权库存轮循并完整播放；成片时长按 `4 秒 hook + 操作素材实际时长 + CTA/口播实际时长` 动态计算，不强制 12 秒。文件使用中文命名并按 `01`、`02`、`03` 排序。
8. 首帧和封面完整复现海报。
9. 幂等上传到 Pending Review 的 `赛后比分` 标签，不自动发布；桌面交付目录为 `每日赛后海报` 和 `赛后比分`。

WorkBuddy 的两批同日产品必须分别传入 `--batch post1` 和 `--batch post2`；对应
`runs/YYYYMMDD_post1` 与 `runs/YYYYMMDD_post2`。两批使用互斥视觉体系和不同文案句式，Phase 5
在上传前比较海报、风格、文案和视频，发现重复立即停止。

海报风格优先保留最新 WorkBuddy 赛后批次已认可的构造：真人球星、强景深、干净戏剧背景和大比分层级。题材由逐场赛后研究决定，可使用正确的胜负情绪、已核实进球者庆祝、裁判向正确犯规方出示红牌或其他有证据的比赛转折；绝不能把赢家与输家的情绪搞反。找不到可验证真人信息时，使用匿名虚拟硬汉球员；项目内球员、队徽和球衣素材按操作员授权处理。
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
CTA 口播库存包含现有女性巴葡声音和一次性生成的 APIMart `gpt-4o-mini-tts`
`onyx` 激情男声；已有文件会直接复用，不会每天重新生成。发布文案固定包含
`Acesse jaguartvbrasil.com/baixar-app para baixar.`，TikTok 的 5 个标签必须包含
`#jaguartv` 与 `#iptv`。

## 使用

```bash
jaguartv-postmatch preflight --config config/local.json
jaguartv-postmatch phase1 --config config/local.json --date 2026-08-31
jaguartv-postmatch run --config config/local.json --date 2026-08-31
jaguartv-postmatch run --config config/local.json --date 2026-08-31 --batch post1
jaguartv-postmatch run --config config/local.json --date 2026-08-31 --batch post2
```

WorkBuddy 可直接粘贴的两份自动化提示词见
[赛后1](docs/workbuddy-automation-prompt-postmatch-1.md) 和
[赛后2](docs/workbuddy-automation-prompt-postmatch-2.md)。

供 WorkBuddy 或人工调用的入口（启动时间由 WorkBuddy 配置）：

```bash
scripts/run-daily.sh
```

素材交付工具可将历史成片、赛前/赛后海报、4-9 秒操作段和末 3 秒 CTA 按 SHA-256 去重：

```bash
jaguartv-media-library --config config/media-library.example.json
```

更多边界和失败策略见 [docs/architecture.md](docs/architecture.md)。
