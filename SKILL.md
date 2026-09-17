---
name: lipvoice-pipeline
display_name: LipVoice 复刻原声换口型
display_name_en: LipVoice Voice-Clone Lip-Sync Pipeline
description: 给一段人物口播底片视频和一段新文案，产出本人形象加本人声音说新台词的成片：克隆底片本人音色配音，云端只重绘嘴部对齐新配音，可剪除长停顿气口，不烧字幕。
description_zh: 用本人声音说新台词。输入口播底片视频和新文案，自动克隆原声、合成配音、云端换口型、剪掉拖沓停顿，输出可直接发布的数字人口播成片。
description_en: Turn any talking-head clip into one where the speaker delivers your new script in their own cloned voice. It clones the timbre from the footage, dubs your lines, time-stretches the audio to fit the runtime, re-renders the mouth with cloud lip-sync, and trims dead air. Same face, same scene, brand-new words. You supply the API key.
category: 内容创作
version: 1.0.0
author: LipVoice
---

# LipVoice 复刻原声换口型流水线

输入「底片视频 + 新文案」，输出「本人形象 + 本人声音 + 说新台词」的成片。
底层服务：阿里云百炼 VideoRetalk（口型替换）+ CosyVoice v2（声音复刻）。

## 运行环境

- Python 3.9 以上，依赖见 requirements.txt（执行 pip install -r requirements.txt）
- ffmpeg 由 imageio-ffmpeg 自带，无需系统安装
- 需要用户自备阿里云百炼 API Key（config.json 的 api_key 或环境变量 DASHSCOPE_API_KEY）

## 命令

```bash
python lipvoice.py doctor                        # 环境自检（免费）
python lipvoice.py estimate --video V --audio A  # 费用预估（不上传、不扣费）
python lipvoice.py run --video 底片.mp4 --script 文案.txt --cut-pauses --yes
python lipvoice.py lipsync --video V --audio A --yes   # 已有配音，只换口型
python lipvoice.py cut-pauses --video V          # 剪掉长停顿（纯本地，0 元）
```

--script 为 UTF-8 纯文本；主脚本 lipvoice.py 与本文件同目录。

## 执行规则（Agent 必读）

1. 确认单铁律：run 与 lipsync 会真实扣费。提交前必须先向用户展示确认单，包含底片文件、文案全文、预估费用（0.08 元每秒）、预计耗时；用户明确确认后才加 --yes 执行。
2. 先自检：新环境先跑 doctor。出现 Arrearage 表示阿里云欠费，提示充值 https://usercenter2.aliyun.com
3. 音色样本：默认抽底片第 8 至 23 秒原声克隆。用户若认为不像，改用清晰无背景音乐的区间（--sample-start 与 --sample-duration），并删除输出目录下 work/voice_id_*.txt 缓存后重跑。
4. 语速：云端 HTTP 接口忽略 speech_rate，脚本用 ffmpeg atempo 变速不变调把配音压进视频时长，不要尝试传语速参数。
5. 异步任务：云端逐段处理，脚本自动轮询；进程被中断不等于任务失败，可用 task_id 重新查询。
6. 字幕：默认不烧字幕。用户需要时另行用 ASR 生成 SRT 文件。

## 费用与耗时

- 只需注册阿里云百炼一家（华北 2 北京地域 Key），不需要其它平台 API
- 换口型 0.08 元每秒（3 分钟约 15 元）；配音约 0.8 至 2 元每万字符；音色登记免费；本地剪辑 0 元
- 新账号有 1800 秒免费额度，但不抵扣欠费，账户余额须为正
- 云端并发仅 1 个任务，3 分钟视频端到端约 20 至 35 分钟

## 合规

声音克隆与肖像使用必须取得本人授权；禁止伪造他人言论。详见 DISCLAIMER.md。

## 已知坑（勿踩）

- requests 必须设置 Session.trust_env = False，否则本机代理会让轮询返回 502 中断
- 提交与登记接口需 X-DashScope-Async: enable、X-DashScope-OssResourceResolve: enable
- 剪气口必须用「分段提取 + concat demuxer」，该 ffmpeg 构建处理长 filter_complex 会报 No such filter
- 视频单边需在 640 至 2048 像素，云端单段上限 120 秒
