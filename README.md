# JaguarTV 任务2赛后比分视频工厂

这是从 Original Content Factory 抽离的独立赛后生产仓库。它只消费任务1已选赛程，完成
赛果核验、赛后研究、Image2 海报、即梦/Seedance 短视频、素材轮循和服务器待审核上传。
它不创建新的比赛筛选清单，也不修改 `Video-creation-automation`。

## 流程

1. 从任务1清单读取稳定比赛 ID、开赛时间和全部频道。
2. 对照 `copa.jarg.top` 与已配置的赛果来源，仅接收 `FT`、`AET`、`PEN`。
3. 使用 agent-reach 收集当前比赛的可追溯赛后证据和参赛球员信息。
4. 通过 Image2 生成 4:5 巴葡赛后海报，Figure 1 固定右上，比分置于脸部安全区下方。
5. 用即梦 VIP / Seedance 生成动态钩子，再由 V7 组装为 12 秒中文命名成片。
6. 首帧和封面完整复现海报；操作片段、CTA、音乐和口播库存轮循。
7. 幂等上传到 Pending Review 的 `赛后比分` 标签，不自动发布。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config/postmatch.example.json config/local.json
```

`config/local.json` 只保存本机路径，不保存密钥。密钥通过环境变量或 macOS Keychain 提供。
仓库不会追踪运行数据库、生成媒体、浏览器会话或上传凭据。

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
