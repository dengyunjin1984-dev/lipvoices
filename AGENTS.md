# AGENTS.md — LipVoice 使用说明（Codex / Cursor / 通用 Agent 平台）

本目录提供一个可执行 CLI：`lipvoice.py`。用自然语言指令驱动即可，不要自己改写底层 API 调用。

## 能力

把「一段人物口播底片视频 + 一段新文案」变成「本人形象 + 本人声音 + 说新台词」的成片。
底层：阿里云百炼 VideoRetalk（口型替换）+ CosyVoice v2（声音复刻）。

## 环境

```bash
pip install -r requirements.txt
cp config.example.json config.json     # 填入 api_key，或设环境变量 DASHSCOPE_API_KEY
python lipvoice.py doctor              # 自检
```

## 命令

```bash
# 全称出片（会扣费，执行前必须向用户确认）
python lipvoice.py run --video <底片.mp4> --script <文案.txt> --cut-pauses --yes

# 仅换口型（已有配音音频）
python lipvoice.py lipsync --video <底片.mp4> --audio <配音.mp3> --yes

# 费用预估（不上传不扣费）
python lipvoice.py estimate --video <底片.mp4> --audio <配音.mp3>

# 剪掉视频里的长停顿（纯本地，免费）
python lipvoice.py cut-pauses --video <视频.mp4>
```

常用参数：`--output-dir`、`--voice-id`（复用音色）、`--sample-start/--sample-duration`（音色样本区间）、`--max-cost`（费用闸门，默认 20 元）、`--seg-len`（默认 110 秒）。

## 铁律

1. **扣费前必须先给用户确认单**：底片文件、文案全文、预估费用（0.08 元/秒）、预计耗时（3 分钟视频约 20–35 分钟）。用户确认后才加 `--yes` 执行。
2. 先跑 `doctor`；报 `Arrearage` 表示阿里云欠费，提示充值 <https://usercenter2.aliyun.com>。
3. 云端任务是异步的，脚本自动轮询；进程中断不等于任务失败。
4. 默认**不烧字幕**。
5. 声音克隆与肖像使用必须取得本人授权，禁止伪造他人言论（见 `DISCLAIMER.md`）。

## 效果调优

- 声音不像 → 换音色样本区间（挑清晰、无背景音乐的 15 秒），删除 `<output-dir>/work/voice_id_*.txt` 后重跑
- 语速 → 云端忽略语速参数，脚本用 ffmpeg `atempo` 变速不变调自动压进视频时长
- 口型不准 → 确保配音不超过视频时长（脚本自动压缩），视频正脸、单边 640–2048px
