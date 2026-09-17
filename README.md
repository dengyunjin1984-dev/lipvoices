# LipVoice — 复刻原声 + 视频换口型流水线

> **English:** Turn any talking-head clip into one where the speaker delivers your new script in their own cloned voice. It clones the timbre from the footage, dubs your lines, time-stretches the audio to fit the runtime, re-renders the mouth with cloud lip-sync, and trims dead air. Same face, same scene, brand-new words. You supply the API key.

给一段**人物口播底片视频**和一段**新文案**，产出「本人形象 + 本人声音 + 说新台词」的成片。
声音来自底片本人的音色克隆，不是平台合成音色；只重绘嘴部区域，其余画面原样保留。

底层服务：阿里云百炼（VideoRetalk 口型替换 + CosyVoice 声音复刻）。**你需要自己的阿里云 Key**，按量计费。

---

## 1. 安装

```bash
pip install -r requirements.txt          # requests、imageio-ffmpeg（自带 ffmpeg，无需系统安装）
cp config.example.json config.json      # 然后填入你的 api_key
```

获取 Key：https://bailian.console.aliyun.com/model/settings/api-key （华北 2 北京地域）
并在模型广场开通 `videoretalk`、`cosyvoice-v2`。

也可以不用 config.json，直接设置环境变量：

```bash
export DASHSCOPE_API_KEY=sk-xxxx        # PowerShell: $env:DASHSCOPE_API_KEY="sk-xxxx"
```

自检：

```bash
python lipvoice.py doctor
```

## 2. 三条常用命令

```bash
# 一条命令出片：复刻原声 -> 配音 -> 换口型 -> 剪气口
python lipvoice.py run --video 底片.mp4 --script 文案.txt --cut-pauses --yes

# 已有配音，只想换口型
python lipvoice.py lipsync --video 底片.mp4 --audio 配音.mp3 --yes

# 只想给已有视频剪掉拖沓的停顿（纯本地，0 元，不需要 Key）
python lipvoice.py cut-pauses --video 视频.mp4
```

## 3. 参数说明

| 参数 | 说明 |
|------|------|
| `--video` | 底片视频（mp4/mov，正脸说话最佳，单边 640–2048px，>2 秒） |
| `--script` | 新文案 txt（UTF-8）；约 300–350 字/分钟 |
| `--cut-pauses` | 剪掉长停顿气口（保留 0.25 秒自然间隙） |
| `--voice-id` | 已登记过的音色 ID，传入可跳过复刻步骤、复用音色 |
| `--sample-start` / `--sample-duration` | 从底片第几秒抽取多少秒原声做音色样本（默认 8s / 15s）。**挑一段清晰、无背景音乐、语速平稳的**，相似度最高 |
| `--max-cost` | 费用闸门，超过则拒绝提交（默认 20 元）；加 `--yes` 表示确认 |
| `--output-dir` | 输出目录，默认 `./lipvoice_outputs` |
| `--seg-len` | 分段长度，默认 110 秒（平台单段上限 120 秒） |

## 4. 需要注册哪些 API？费用多少？

**只需要注册一家：阿里云百炼（DashScope）。** 不需要 OpenAI / DeepSeek / 火山引擎 / 任何其它平台的 Key。
（如果你只是想把自己的技能上架售卖，那是另一回事——分发平台如 WorkBuddy 开放平台、SkillMarket 的**卖家账号注册是免费的**，与运行时的 API 无关。）

### 4.1 要开通的东西

| 项目 | 在哪开 | 是否收费 |
|------|--------|---------|
| 阿里云账号 + 实名认证 | https://www.aliyun.com | 免费 |
| 百炼 API Key | https://bailian.console.aliyun.com/model/settings/api-key （**必须华北 2 北京地域**） | 免费 |
| 模型 `videoretalk` | 百炼控制台 → 模型广场 → 搜索开通 | 按量计费 |
| 模型 `cosyvoice-v2` + `voice-enrollment` | 同上 | 按量计费 |

### 4.2 计费明细（2026-09 官方口径）

| 项目 | 单价 | 1000 字文案 / 3 分钟视频 |
|------|------|------------------------|
| 换口型 `videoretalk` | **0.08 元/秒**（按生成视频时长后付费） | 188 秒 ≈ **15.0 元** |
| 配音 `cosyvoice-v2` | 约 0.8–2 元/万字符（以控制台标价为准） | 约 0.1–0.2 元 |
| 音色登记 `voice-enrollment` | 登记音色免费 | 0 元 |
| 剪气口 / 变速 / 拼接 | 本地 ffmpeg | **0 元** |

**一条 3 分钟口播视频，实付约 15 元。**

### 4.3 三条重要的费用规则

1. **免费额度 1800 秒**：新账号开通后有 1800 秒（约 30 分钟）免费额度，有效期通常 180 天，够白嫖约 10 条 3 分钟视频。
2. **免费额度不抵扣欠费**：账户余额必须是正数，余额为 0 会直接报 `Arrearage`（欠费）拒绝调用。建议先充 50 元。充值：https://usercenter2.aliyun.com
3. **并发限制**：`videoretalk` 同时只处理 1 个任务、提交 RPS 为 1，多余任务排队。所以长视频要分段串行处理，这也是 3 分钟视频要跑 20–35 分钟的原因，属正常现象，不是卡死。

## 5. 出片耗时

3 分钟视频约 **20–35 分钟**（切段上传 + 云端逐段推理 + 拼接），属正常异步任务，脚本会打印实时状态。

## 6. 合规声明（重要）

- 声音克隆与肖像使用**必须取得本人书面授权**
- 严禁用于伪造他人言论、诈骗、造谣、冒充身份
- 发布平台（抖音/视频号/YouTube 等）通常要求 AI 生成内容标注，请自行遵守
- 详见 `DISCLAIMER.md`

## 7. 常见问题

**Q：声音不像本人？**
A：换一段更好的样本重试。用 `--sample-start 30 --sample-duration 15` 换样本区间，删掉 `outputs/work/voice_id_*.txt` 缓存后重跑。

**Q：语速太慢/太快？**
A：云端 HTTP 接口会忽略语速参数，脚本用 `atempo` 变速不变调自动把配音压进视频时长。想更慢就换更长的底片视频。

**Q：口型对不上？**
A：确认配音时长不超过视频时长（脚本会自动压缩）。超出会被截断。

**Q：报 `Arrearage`？**
A：阿里云账户欠费，去费用中心充值。

**Q：代理环境报错 / 轮询 502？**
A：脚本已默认绕过系统代理（`trust_env=False`）。若公司网络强制代理，需自行在代码里配置。
