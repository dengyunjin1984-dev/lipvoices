#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LipVoice - 复刻原声 + 视频换口型一体化流水线（阿里云百炼 API）。

把「一段底片视频 + 一段新文案」变成「本人形象 + 本人声音 + 说新台词」的成片。

命令：
  doctor        环境 / 密钥自检（免费）
  estimate      预估费用（不上传、不扣费）
  run           全流程：复刻音色 -> 全文配音 -> 变速适配 -> 换口型 -> 剪气口
  lipsync       仅换口型（已有底片视频 + 配音音频）
  dub           仅配音（用已登记的音色 ID 合成文案）
  cut-pauses    仅剪气口（对任意本地视频，纯离线，0 元）

配置优先级：--api-key > 环境变量 DASHSCOPE_API_KEY > config.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# 绕开系统代理：dashscope 国内域名直连更快，代理曾导致轮询 502 中断
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "*"

PKG_DIR = Path(__file__).resolve().parent
CFG_PATH = PKG_DIR / "config.json"

DEFAULTS = {
    "api_key": "",
    # 国内（北京，默认）           https://dashscope.aliyuncs.com
    # 国际（新加坡）               https://dashscope-intl.aliyuncs.com
    "base_url": "https://dashscope.aliyuncs.com",
    "model": "videoretalk",
    "tts_model": "cosyvoice-v2",
    "price_per_second": 0.08,
    "default_seg_len": 110,
    "default_max_cost": 20,
    "default_output_dir": "./lipvoice_outputs",
    "voice_prefix": "user",
    "sample_start": 8.0,
    "sample_duration": 15.0,
}

SUBMIT_PATH = "/api/v1/services/aigc/image2video/video-synthesis"
TASK_PATH = "/api/v1/tasks/{task_id}"
UPLOAD_PATH = "/api/v1/uploads"
TTS_PATH = "/api/v1/services/audio/tts/SpeechSynthesizer"
VOICE_PATH = "/api/v1/services/audio/tts/customization"

VIDEO_LIMITS = {"size_mb": 300, "dur_min": 2.0, "dur_max": 120.0,
                "side_min": 640, "side_max": 2048}
AUDIO_LIMITS = {"size_mb": 30, "dur_min": 2.0, "dur_max": 120.0}


# ---------------------------------------------------------------- 基础设施
def load_config():
    cfg = dict(DEFAULTS)
    if CFG_PATH.is_file():
        try:
            cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"[警告] 读取 config.json 失败：{e}")
    return cfg


def resolve_api_key(args, cfg):
    key = (getattr(args, "api_key", None)
           or cfg.get("api_key")
           or os.environ.get("DASHSCOPE_API_KEY", ""))
    key = (key or "").strip()
    if not key:
        raise SystemExit(
            "未找到阿里云百炼 API Key。\n"
            "  方式一（推荐）：cp config.example.json config.json，填入 api_key\n"
            "  方式二：设置环境变量 DASHSCOPE_API_KEY\n"
            "获取地址：https://bailian.console.aliyuncs.com/model/settings/api-key"
        )
    return key


def session():
    import requests
    s = requests.Session()
    s.trust_env = False
    return s


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_cmd(cmd, desc=None):
    if desc:
        print(f"[cmd] {desc}", flush=True)
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        tail = "\n".join((res.stdout or "").strip().splitlines()[-12:])
        raise SystemExit(f"命令执行失败（exit={res.returncode}）：\n{tail}")
    return res.stdout or ""


def probe_media(path):
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"文件不存在：{path}")
    res = subprocess.run([ffmpeg_exe(), "-i", str(p)], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    out = res.stdout or ""
    info = {"path": str(p), "size_mb": p.stat().st_size / 1024 / 1024,
            "duration": 0.0, "width": 0, "height": 0, "fps": 0.0}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", out)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"(\d{2,5})x(\d{2,5})", out)
    if m:
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    m = re.search(r"(\d+\.?\d*)\s*fps", out)
    if m:
        info["fps"] = float(m.group(1))
    return info


def human(sec):
    sec = float(sec)
    return f"{int(sec // 60)}分{sec % 60:04.1f}秒"


# ---------------------------------------------------------------- 云端交互
def upload_file(sess, api_key, base_url, path, model):
    r = sess.get(f"{base_url}{UPLOAD_PATH}",
                 params={"action": "getPolicy", "model": model},
                 headers={"Authorization": f"Bearer {api_key}",
                          "Content-Type": "application/json"},
                 timeout=60)
    try:
        d = r.json()["data"]
    except Exception:
        raise SystemExit(f"获取上传凭证失败（HTTP {r.status_code}）：{r.text[:400]}")
    key = f"{d['upload_dir']}/{Path(path).name}"
    with open(path, "rb") as f:
        files = {
            "OSSAccessKeyId": (None, d["oss_access_key_id"]),
            "Signature": (None, d["signature"]),
            "policy": (None, d["policy"]),
            "x-oss-object-acl": (None, d["x_oss_object_acl"]),
            "x-oss-forbid-overwrite": (None, d["x_oss_forbid_overwrite"]),
            "key": (None, key),
            "success_action_status": (None, "200"),
            "file": (Path(path).name, f, "application/octet-stream"),
        }
        up = sess.post(d["upload_host"], files=files, timeout=1800)
    if up.status_code not in (200, 204):
        raise SystemExit(f"上传失败（HTTP {up.status_code}）：{up.text[:300]}")
    return f"oss://{key}"


def submit_task(sess, api_key, base_url, model, video_url, audio_url, ref_image=None):
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json",
               "X-DashScope-Async": "enable",
               "X-DashScope-OssResourceResolve": "enable"}
    payload = {"model": model,
               "input": {"video_url": video_url, "audio_url": audio_url},
               "parameters": {"video_extension": False}}
    if ref_image:
        payload["input"]["ref_image_url"] = ref_image
    r = sess.post(f"{base_url}{SUBMIT_PATH}", headers=headers,
                  data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  timeout=120)
    try:
        tid = r.json()["output"]["task_id"]
    except Exception:
        raise SystemExit(f"提交任务失败（HTTP {r.status_code}）：{r.text[:400]}")
    print(f"  任务已提交：{tid}", flush=True)
    return tid


def poll_task(sess, api_key, base_url, task_id, interval=10, timeout=5400):
    headers = {"Authorization": f"Bearer {api_key}"}
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = sess.get(f"{base_url}{TASK_PATH.format(task_id=task_id)}",
                     headers=headers, timeout=60)
        try:
            out = r.json().get("output", {})
        except Exception:
            raise SystemExit(f"查询任务失败（HTTP {r.status_code}）：{r.text[:300]}")
        status = out.get("task_status", "UNKNOWN")
        if status != last:
            print(f"  状态：{status}（已等待 {int(time.time() - t0)}s）", flush=True)
            last = status
        if status == "SUCCEEDED":
            usage = r.json().get("usage", {}) or {}
            return out.get("video_url", ""), float(usage.get("video_duration") or 0)
        if status in ("FAILED", "UNKNOWN"):
            raise SystemExit(f"任务失败：{status} {out.get('code', '')} {out.get('message', '')}")
        time.sleep(interval)
    raise SystemExit("任务超时，请稍后用 task_id 手动查询")


def download(sess, url, out_path):
    r = sess.get(url, stream=True, timeout=1800)
    if r.status_code != 200:
        raise SystemExit(f"下载成片失败（HTTP {r.status_code}）")
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)


def concat_segments(seg_paths, out_path):
    if len(seg_paths) == 1:
        shutil.copyfile(seg_paths[0], out_path)
        return
    list_file = Path(out_path).with_suffix(".concat.txt")
    list_file.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in seg_paths),
                         encoding="utf-8")
    run_cmd([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(out_path)])
    list_file.unlink(missing_ok=True)


# ---------------------------------------------------------------- 音色复刻 / 配音
def register_voice(sess, api_key, cfg, video, work, prefix=None,
                   start=None, dur=None):
    """从底片视频抽一段原声，登记 cosyvoice-v2 复刻音色（同底片缓存复用）。"""
    start = DEFAULTS["sample_start"] if start is None else start
    dur = DEFAULTS["sample_duration"] if dur is None else dur
    prefix = prefix or cfg["voice_prefix"]
    cache = work / f"voice_id_{Path(video).stem}.txt"
    if cache.is_file():
        vid = cache.read_text(encoding="utf-8").strip()
        print(f"== 复用已登记音色 {vid}", flush=True)
        return vid
    print("== 音色复刻（抽原声样本 -> 登记 cosyvoice-v2）==", flush=True)
    sample = work / "voice_ref.mp3"
    run_cmd([ffmpeg_exe(), "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", str(video),
             "-vn", "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k",
             str(sample)], "抽取原声样本")
    url = upload_file(sess, api_key, cfg["base_url"], sample, "voice-enrollment")
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json",
               "X-DashScope-OssResourceResolve": "enable"}
    payload = {"model": "voice-enrollment",
               "input": {"action": "create_voice",
                         "target_model": cfg["tts_model"],
                         "prefix": prefix, "url": url}}
    r = sess.post(f"{cfg['base_url']}{VOICE_PATH}", headers=headers,
                  data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  timeout=120)
    vid = (r.json().get("output") or {}).get("voice_id", "")
    if not vid:
        raise SystemExit(f"音色登记失败：{r.text[:400]}")
    cache.write_text(vid, encoding="utf-8")
    print(f"  音色 ID：{vid}", flush=True)
    return vid


def synthesize(sess, api_key, cfg, voice_id, text, work, name="dub"):
    """用复刻音色合成配音。注意：HTTP 非流式接口会忽略 speech_rate，语速靠后续 atempo。"""
    print("== 全文配音 ==", flush=True)
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json",
               "X-DashScope-OssResourceResolve": "enable"}
    payload = {"model": cfg["tts_model"],
               "input": {"text": text, "voice": voice_id},
               "parameters": {"format": "mp3", "sample_rate": 22050}}
    r = sess.post(f"{cfg['base_url']}{TTS_PATH}", headers=headers,
                  data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  timeout=900)
    url = ((r.json().get("output") or {}).get("audio") or {}).get("url", "")
    if not url:
        raise SystemExit(f"配音失败：{r.text[:500]}")
    out = work / f"{name}.mp3"
    download(sess, url, out)
    print(f"  配音 {probe_media(out)['duration']:.1f}s", flush=True)
    return out


def fit_duration(dub, target_dur, work):
    """配音超过目标时长则 atempo 变速不变调压缩（音色、音高不变）。"""
    dur = probe_media(dub)["duration"]
    if dur <= target_dur:
        print(f"== 时长适配：无需变速（{dur:.1f}s <= {target_dur:.1f}s）", flush=True)
        return dub
    tempo = round(dur / target_dur, 4)
    out = work / "dub_fit.mp3"
    run_cmd([ffmpeg_exe(), "-y", "-i", str(dub), "-filter:a", f"atempo={tempo}",
             "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k",
             str(out)], f"变速不变调 x{tempo}")
    print(f"== 时长适配：{dur:.1f}s -> {probe_media(out)['duration']:.1f}s", flush=True)
    return out


# ---------------------------------------------------------------- 换口型 / 剪气口
def plan_segments(video_dur, seg_len):
    if video_dur <= seg_len:
        return [(0.0, video_dur)]
    segs, t = [], 0.0
    while t < video_dur - 0.5:
        d = min(seg_len, video_dur - t)
        segs.append((round(t, 3), round(d, 3)))
        t += d
    return segs


def lipsync(sess, api_key, cfg, video, audio, out_dir, seg_len, max_cost,
            yes=False, ref_image=None):
    """切段 -> 上传 -> 提交 -> 轮询 -> 下载 -> 拼接。"""
    v = probe_media(video)
    a = probe_media(audio)
    bill = min(v["duration"], a["duration"])
    cost = bill * cfg["price_per_second"]
    print(f"== 换口型：视频 {human(v['duration'])}／音频 {human(a['duration'])} | "
          f"预估 {cost:.2f} 元", flush=True)
    if cost > max_cost and not yes:
        raise SystemExit(f"预估 {cost:.2f} 元超过上限 {max_cost} 元，确认请加 --yes")
    side = max(v["width"], v["height"])
    if v["width"] and not (VIDEO_LIMITS["side_min"] <= side <= VIDEO_LIMITS["side_max"]):
        raise SystemExit(f"视频边长 {v['width']}x{v['height']} 超出 640-2048，请先缩放")
    if a["duration"] < AUDIO_LIMITS["dur_min"]:
        raise SystemExit("音频过短（需 >2 秒）")

    segs = plan_segments(v["duration"], seg_len)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="lipvoice_"))
    stamp = time.strftime("%Y%m%d_%H%M%S")
    try:
        results, billed = [], 0.0
        for i, (start, dur) in enumerate(segs, 1):
            print(f"== 第 {i}/{len(segs)} 段 {start:.0f}s~{start + dur:.0f}s ==", flush=True)
            vp = work / f"v_{i:02d}.mp4"
            ap = work / f"a_{i:02d}.mp3"
            run_cmd([ffmpeg_exe(), "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", str(video),
                     "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                     "-pix_fmt", "yuv420p"] + (["-r", f"{v['fps']:.3f}"] if v["fps"] else [])
                    + [str(vp)], "切视频段")
            if vp.stat().st_size / 1024 / 1024 > VIDEO_LIMITS["size_mb"]:
                run_cmd([ffmpeg_exe(), "-y", "-i", str(vp), "-an",
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                         "-pix_fmt", "yuv420p", str(work / f"v_{i:02d}_s.mp4")], "压缩分段")
                vp = work / f"v_{i:02d}_s.mp4"
            run_cmd([ffmpeg_exe(), "-y", "-ss", f"{start}", "-t", f"{dur}", "-i", str(audio),
                     "-vn", "-ar", "16000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "128k",
                     str(ap)], "切音频段")
            vu = upload_file(sess, api_key, cfg["base_url"], vp, cfg["model"])
            au = upload_file(sess, api_key, cfg["base_url"], ap, cfg["model"])
            tid = submit_task(sess, api_key, cfg["base_url"], cfg["model"], vu, au,
                              ref_image=ref_image)
            url, d = poll_task(sess, api_key, cfg["base_url"], tid)
            billed += d
            rp = work / f"r_{i:02d}.mp4"
            download(sess, url, rp)
            results.append(rp)
        final = out_dir / f"{Path(video).stem}_lipvoice_{stamp}.mp4"
        concat_segments(results, final)
        real = billed * cfg["price_per_second"]
        print(f"成片：{final}", flush=True)
        print(f"时长：{human(probe_media(final)['duration'])} | "
              f"体积：{final.stat().st_size / 1024 / 1024:.1f}MB", flush=True)
        print(f"实际计费：{billed:.1f}s ≈ {real:.2f} 元", flush=True)
        return str(final)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cut_pauses(src, keep=0.25, min_sil=0.35, noise="-30dB"):
    """剪除长停顿气口。必须分段提取 + concat（该 ffmpeg 构建的长 filter_complex 会报错）。"""
    src = Path(src)
    res = subprocess.run([ffmpeg_exe(), "-i", str(src), "-af",
                          f"silencedetect=noise={noise}:d={min_sil}", "-f", "null", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace")
    events = re.findall(r"silence_(start|end):\s*([\d.]+)", res.stdout or "")
    v_dur = probe_media(src)["duration"]
    silences, cur = [], None
    for kind, val in events:
        val = float(val)
        if kind == "start":
            cur = val
        elif kind == "end" and cur is not None:
            silences.append((cur, val))
            cur = None
    if cur is not None:
        silences.append((cur, v_dur))

    ranges, t, n = [], 0.0, 0
    for s, e in silences:
        if e - s <= 2 * keep + 0.05:
            continue
        if s > t:
            ranges.append((t, s + keep))
        t = max(t, e - keep)
        n += 1
    if t < v_dur:
        ranges.append((t, v_dur))
    removed = v_dur - sum(b - a for a, b in ranges)
    if removed < 1.0 or len(ranges) < 2:
        print(f"== 可剪停顿 {removed:.1f}s 不足 1s，跳过", flush=True)
        return str(src)

    workdir = Path(tempfile.mkdtemp(prefix="lipvoice_cut_"))
    try:
        segs = []
        for i, (a, b) in enumerate(ranges):
            seg = workdir / f"seg_{i:02d}.mp4"
            run_cmd([ffmpeg_exe(), "-y", "-ss", f"{a:.3f}", "-i", str(src),
                     "-t", f"{b - a:.3f}", "-c:v", "libx264", "-preset", "veryfast",
                     "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                     str(seg)], f"段 {i + 1}/{len(ranges)}")
            segs.append(seg)
        lst = workdir / "list.txt"
        lst.write_text("".join(f"file '{Path(s).as_posix()}'\n" for s in segs), encoding="utf-8")
        out = src.with_name(src.stem + "_cut.mp4")
        run_cmd([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c", "copy", str(out)], "concat 拼接")
        print(f"== 剪除 {n} 处长停顿，{v_dur:.1f}s -> "
              f"{probe_media(out)['duration']:.1f}s -> {out}", flush=True)
        return str(out)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------- 命令
def cmd_doctor(args):
    print("== LipVoice 环境自检 ==")
    cfg = load_config()
    ok = True
    for mod in ("requests", "imageio_ffmpeg"):
        try:
            __import__(mod)
            print(f"[OK] 依赖 {mod}")
        except Exception as e:
            print(f"[缺失] 依赖 {mod}：{e}")
            ok = False
    try:
        print(f"[OK] ffmpeg：{ffmpeg_exe()}")
    except Exception as e:
        print(f"[缺失] ffmpeg：{e}（执行 pip install imageio-ffmpeg）")
        ok = False

    key = args.api_key or cfg.get("api_key") or os.environ.get("DASHSCOPE_API_KEY", "")
    if not key.strip():
        print("[缺失] API Key 未配置（config.json 或环境变量 DASHSCOPE_API_KEY）")
        ok = False
    else:
        print(f"[OK] API Key 已配置（{key[:6]}...{key[-4:]}）")
        try:
            s = session()
            r = s.get(f"{cfg['base_url']}/api/v1/uploads",
                      params={"action": "getPolicy", "model": cfg["model"]},
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      timeout=30)
            if r.status_code == 200 and "data" in r.json():
                print("[OK] 密钥可用，已开通 videoretalk 权限")
            elif "Arrearage" in r.text or "arrearage" in r.text:
                print("[警告] 账户欠费，请充值 https://usercenter2.aliyun.com")
                ok = False
            else:
                print(f"[警告] 校验返回 HTTP {r.status_code}：{r.text[:200]}")
        except Exception as e:
            print(f"[警告] 网络校验失败：{e}")
    print(f"[信息] 换口型单价 {cfg['price_per_second']} 元/秒")
    print("== 自检结果：" + ("全部通过 ==" if ok else "存在问题，见上 =="))
    return 0 if ok else 1


def cmd_estimate(args):
    cfg = load_config()
    v = probe_media(args.video)
    a = probe_media(args.audio)
    bill = min(v["duration"], a["duration"])
    segs = plan_segments(v["duration"], args.seg_len or cfg["default_seg_len"])
    print("== 输入 ==")
    print(f"视频：{v['duration']:.1f}s | {v['width']}x{v['height']} | {v['fps']:.2f}fps")
    print(f"音频：{a['duration']:.1f}s")
    print(f"== 分段：{len(segs)} 段 ==")
    for i, (s, d) in enumerate(segs, 1):
        print(f"  第{i}段 {s:.0f}s~{s + d:.0f}s")
    cost = bill * cfg["price_per_second"]
    print(f"== 预估费用：{bill:.1f}s x {cfg['price_per_second']} = {cost:.2f} 元 ==")
    warn = []
    if a["duration"] > v["duration"] + 0.5:
        warn.append("音频比视频长，成片按视频长度截断")
    if v["width"] and not (VIDEO_LIMITS["side_min"] <= max(v["width"], v["height"]) <= VIDEO_LIMITS["side_max"]):
        warn.append(f"视频边长 {v['width']}x{v['height']} 不在 640-2048")
    for w in warn:
        print(f"  ! {w}")
    return 0


def cmd_dub(args):
    cfg = load_config()
    api_key = resolve_api_key(args, cfg)
    sess = session()
    out_dir = Path(args.output_dir or cfg["default_output_dir"])
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    text = Path(args.script).read_text(encoding="utf-8").strip()
    voice_id = args.voice_id or register_voice(
        sess, api_key, cfg, args.video, work,
        prefix=args.voice_prefix, start=args.sample_start, dur=args.sample_duration)
    dub = synthesize(sess, api_key, cfg, voice_id, text, work)
    if args.fit_to and args.fit_to > 0:
        dub = fit_duration(dub, args.fit_to, work)
    print(f"配音：{dub}")
    return 0


def cmd_lipsync(args):
    cfg = load_config()
    api_key = resolve_api_key(args, cfg)
    sess = session()
    lipsync(sess, api_key, cfg, args.video, args.audio,
            args.output_dir or cfg["default_output_dir"],
            args.seg_len or cfg["default_seg_len"], args.max_cost,
            yes=args.yes, ref_image=args.ref_image)
    return 0


def cmd_run(args):
    cfg = load_config()
    api_key = resolve_api_key(args, cfg)
    sess = session()
    out_dir = Path(args.output_dir or cfg["default_output_dir"])
    (out_dir / "work").mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    text = Path(args.script).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("文案文件为空")

    print("== [1/5] 音色复刻 ==", flush=True)
    voice_id = args.voice_id or register_voice(
        sess, api_key, cfg, args.video, work,
        prefix=args.voice_prefix, start=args.sample_start, dur=args.sample_duration)

    print("== [2/5] 全文配音 ==", flush=True)
    dub = synthesize(sess, api_key, cfg, voice_id, text, work)

    print("== [3/5] 时长适配 ==", flush=True)
    v_dur = probe_media(args.video)["duration"]
    dub = fit_duration(dub, max(v_dur - 3.0, 2.0), work)

    print("== [4/5] 换口型 ==", flush=True)
    final = lipsync(sess, api_key, cfg, args.video, dub, out_dir,
                    args.seg_len or cfg["default_seg_len"], args.max_cost,
                    yes=args.yes, ref_image=args.ref_image)

    if args.cut_pauses:
        print("== [5/5] 剪气口 ==", flush=True)
        final = cut_pauses(final)
    print(f"== 完成：{final}", flush=True)
    return 0


def cmd_cut(args):
    out = cut_pauses(args.video)
    print(f"输出：{out}")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="lipvoice", description="复刻原声 + 视频换口型流水线")
    p.add_argument("--api-key", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="环境/密钥自检")
    d.set_defaults(func=cmd_doctor)

    e = sub.add_parser("estimate", help="预估费用")
    e.add_argument("--video", required=True)
    e.add_argument("--audio", required=True)
    e.add_argument("--seg-len", type=float, default=None)
    e.set_defaults(func=cmd_estimate)

    r = sub.add_parser("run", help="全流程出片")
    r.add_argument("--video", required=True, help="底片视频")
    r.add_argument("--script", required=True, help="新文案 txt")
    r.add_argument("--output-dir", default=None)
    r.add_argument("--voice-id", default=None, help="已登记音色（跳过复刻）")
    r.add_argument("--voice-prefix", default=None)
    r.add_argument("--sample-start", type=float, default=None, help="原声样本起点秒")
    r.add_argument("--sample-duration", type=float, default=None, help="原声样本时长秒")
    r.add_argument("--seg-len", type=float, default=None)
    r.add_argument("--max-cost", type=float, default=None)
    r.add_argument("--ref-image", default=None)
    r.add_argument("--cut-pauses", action="store_true", help="剪除长停顿气口")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("lipsync", help="仅换口型（需已有配音）")
    l.add_argument("--video", required=True)
    l.add_argument("--audio", required=True)
    l.add_argument("--output-dir", default=None)
    l.add_argument("--seg-len", type=float, default=None)
    l.add_argument("--max-cost", type=float, default=None)
    l.add_argument("--ref-image", default=None)
    l.add_argument("--yes", action="store_true")
    l.set_defaults(func=cmd_lipsync)

    u = sub.add_parser("dub", help="仅配音")
    u.add_argument("--video", required=True, help="提供原声样本的底片视频")
    u.add_argument("--script", required=True)
    u.add_argument("--output-dir", default=None)
    u.add_argument("--voice-id", default=None)
    u.add_argument("--voice-prefix", default=None)
    u.add_argument("--sample-start", type=float, default=None)
    u.add_argument("--sample-duration", type=float, default=None)
    u.add_argument("--fit-to", type=float, default=None, help="压缩到该秒数以内")
    u.set_defaults(func=cmd_dub)

    c = sub.add_parser("cut-pauses", help="剪除视频长停顿（纯离线）")
    c.add_argument("--video", required=True)
    c.set_defaults(func=cmd_cut)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = load_config()
    if getattr(args, "max_cost", None) is None:
        args.max_cost = cfg["default_max_cost"]
    args.api_key = getattr(args, "api_key", None) or cfg.get("api_key") or None
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
