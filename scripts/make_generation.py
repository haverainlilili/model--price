#!/usr/bin/env python3
"""Initialize official-only image/video generation facts before first model extraction.

Every numeric value below is traceable to a first-party page listed in the matching
YAML catalog.  Re-running refreshes only seed records; a successful automatic
extraction (source=official) is never overwritten unless --force is passed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = "latest official snapshot · 2026-09-08"


def image(name, api, modes, pricing=None, currency=None, per_image=None,
          group=None, basis=None, resolution=None, ratios=None, formats=None,
          free=None, note=None, region=None, status="active", sunset=None):
    return {
        "name": name, "variant_key": name, "api_available": api, "modes": modes,
        "pricing": pricing, "currency": currency,
        "price_per_image": per_image, "comparison_group": group,
        "price_basis": basis, "resolution": resolution,
        "aspect_ratios": ratios, "output_formats": formats,
        "comparison_width": None, "comparison_height": None,
        "quality_tier": None,
        "lifecycle_status": status, "sunset_at": sunset,
        "region": (region or ("domestic" if currency == "CNY" else "intl")),
        "free_quota": free, "note": note,
    }


def video(name, api, modes, pricing=None, currency=None, per_second=None,
          group=None, basis=None, resolution=None, duration=None, fps=None,
          ratios=None, audio=None, free=None, note=None, region=None,
          status="active", sunset=None):
    return {
        "name": name, "variant_key": name, "api_available": api, "modes": modes,
        "pricing": pricing, "currency": currency,
        "price_per_second": per_second, "comparison_group": group,
        "price_basis": basis, "resolution": resolution, "duration": duration,
        "frame_rate": fps, "aspect_ratios": ratios, "native_audio": audio,
        "comparison_resolution": None,
        "lifecycle_status": status, "sunset_at": sunset,
        "region": (region or ("domestic" if currency == "CNY" else "intl")),
        "free_quota": free, "note": note,
    }


IMAGE = {
    "openai": [image(
        "gpt-image-2", True,
        ["text-to-image", "image-edit", "reference"],
        "图片输入 $8/百万 tokens、缓存输入 $2；图片输出 $30/百万 tokens；1024² medium 输出约 $0.053/张，另计输入",
        "USD", basis="输出 token 价，不是完整单次请求价",
        resolution="边长≤3840 且为 16 的倍数；总像素 655,360–8,294,400",
        ratios="最长边:最短边 ≤ 3:1", formats="PNG / JPEG / WebP",
        free="Free tier 不支持",
        note="单张总价随输入、尺寸与质量变化，因此不进入固定单张价柱图。")],
    "google": [
        image("Gemini 3.1 Flash-Lite Image", True,
              ["text-to-image", "image-edit", "reference"],
              "1K 图片输出约 $0.0336/张，另计文本/图片输入；图片输出 $30/百万 tokens",
              "USD", basis="输出 token 价，不是完整单次请求价", resolution="1K",
              ratios="多种比例", formats="PNG / JPEG",
              free="图片模型 API Free Tier 不可用",
              note="当前 Gemini Image 按 token 计费，不进固定单张价柱图。"),
        image("Gemini 3.1 Flash Image（Nano Banana 2）", True,
              ["text-to-image", "image-edit", "reference"],
              "图片输出 $60/百万 tokens，另计文本/图片输入",
              "USD", basis="输出 token 价，不是完整单次请求价", resolution="1K / 2K / 4K",
              ratios="多种比例", formats="PNG / JPEG（官方示例）",
              free="图片模型 API Free Tier 不可用",
              note="官方 isometric-pool 样图属于本模型；Imagen 4 已于 2026-08-17 停用。")],
    "xai": [image(
        "grok-imagine-image-2.0", True,
        ["text-to-image", "image-edit", "reference"],
        "$0.06/张（1K medium，API 默认 quality）", "USD", .06,
        "usd-standard-1mp", "1K · medium（API 默认）· 无参考图文生图",
        "1K / 2K", "1:1、16:9、9:16、4:3、3:4、3:2、2:3 等",
        "URL / Base64", note="1K low 为 $0.04/张；参考图另收 $0.01/张；2K 价格更高。")],
    "bfl": [image(
        "FLUX.2 Klein / Pro / Max", True,
        ["text-to-image", "image-edit", "reference"],
        "Klein 4B from $0.014；Pro from $0.03；Max from $0.07/张",
        "USD", basis="官网标注 from / MP，无法锁定统一 1024²总价",
        resolution="最高 4MP，边长为 16 的倍数", formats="JPEG / PNG / WebP",
        note="官网示例确认 Klein 4B 2MP=$0.015；因按像素且使用“from”，不进入严格柱图。")],
    "stability": [image(
        "Stable Image Ultra", True, ["text-to-image", "image-to-image"],
        "8 credits/张；1 credit=$0.01", "USD", .08,
        "usd-premium-1mp", "Ultra · 1:1 / 1K-class · PAYG",
        "1K-class", "含 1:1 等 9 种比例", "JPEG / PNG / WebP",
        free="新账户 25 credits", note="独立编辑/控制接口通常另计 4–8 credits。")],
    "adobe": [
        image("Firefly Image 5 Generate Image API", True,
              ["text-to-image", "image-edit", "reference"],
              "企业授权 / 联系销售；消费者 generative credits 不是 API 单价",
              basis="Generate Image API 无公开自助单张价", resolution="原生最高 4MP",
              note="Image 5 的 instruct edit 使用 referenceBlobs。"),
        image("Firefly Fill Image API", True, ["inpainting"],
              "企业授权 / 联系销售", basis="独立 Fill Image API；无公开自助单张价",
              note="与 Image 5 Generate Image 端点分列。"),
        image("Firefly Expand Image API", True, ["outpainting"],
              "企业授权 / 联系销售", basis="独立 Expand Image API；无公开自助单张价",
              note="与 Image 5 Generate Image 端点分列。")],
    "ideogram": [
        image("Ideogram 4 Default", True,
              ["text-to-image", "image-edit", "reference", "outpainting"],
              "$0.06/张", "USD", .06, "usd-standard-1mp",
              "V4 Default · 1K 正方形", "1K-class", "多种比例",
              "URL（临时）", note="Remix、Edit、Reframe、换背景适用官网 API 价表。"),
        image("Ideogram 4 Quality", True,
              ["text-to-image", "image-edit", "reference"],
              "$0.10/张", "USD", .10, "usd-premium-1mp",
              "V4 Quality · 1K 正方形", "1K-class", "多种比例",
              "URL（临时）")],
    "recraft": [
        image("Recraft V4.1 Raster", True,
              ["text-to-image", "image-to-image", "inpainting", "outpainting", "reference"],
              "35 units=$0.035/张", "USD", .035, "usd-standard-1mp",
              "V4.1 raster · 1024×1024", "1024×1024", formats="PNG / WebP / JPEG",
              note="$1=1000 units；矢量输出另有独立价格。"),
        image("Recraft V4.1 Pro", True,
              ["text-to-image", "reference"], "$0.21/张", "USD", .21,
              basis="Pro · 2048×2048（非 1MP，不进柱图）", resolution="2048×2048")],
    "leonardo": [image(
        "Leonardo Native Models API", True,
        ["text-to-image", "image-to-image", "reference", "inpainting"],
        "PAYG；准确费用仅登录后计算器及响应 cost.amount 可见",
        basis="无公开静态模型单价表", resolution="模型相关；Lucid Origin 默认 1200²",
        free="$5 API credit", note="消费者每日 tokens 与 API credit 分开。")],
    "midjourney": [image(
        "Midjourney V8.2", False,
        ["text-to-image", "image-edit", "reference", "inpainting", "outpainting"],
        "非 API 订阅：$10 / $30 / $60 / $120 每月", "USD",
        basis="官网/Discord 订阅，不能折算 API 单张价", resolution="SD 1024²；HD 2048²",
        free="无网页/Discord 免费试用",
        note="官方明确不提供公开 API，且禁止未经明确许可的自动化。")],
    "seedream": [
        image("Seedream 5.0 Lite", True,
              ["text-to-image", "image-edit", "reference"], "¥0.22/张", "CNY", .22,
              basis="Lite · 原生 2K/3K/4K（非 1MP，不进柱图）", resolution="2K / 3K / 4K",
              formats="PNG / JPEG", note="可选联网搜索；最多 14 张参考图。"),
        image("Seedream 5.0 Pro（中国区）", True,
              ["text-to-image", "image-edit", "reference"], "≤2.61MP ¥0.30/输出图；后续参考图 ¥0.02/张",
              "CNY", .30, "cny-premium-1mp", "Pro · 中国区 · 1K · 文生图无参考图",
              "1K / 1.5K / 2K", "1:16–16:1"),
        image("Seedream 5.0 Pro（BytePlus）", True,
              ["text-to-image", "image-edit", "reference"], "≤2.61MP $0.045/输出图；后续参考图 $0.003/张",
              "USD", .045, "usd-premium-1mp", "Pro · BytePlus · 1K · 文生图无参考图",
              "1K / 1.5K / 2K", "1:16–16:1")],
    "alibaba-wan": [
        image("Wan 2.7 Image（中国区）", True,
              ["text-to-image", "image-edit", "reference"], "¥0.20/输出图", "CNY", .20,
              "cny-standard-1mp", "Wan 2.7 · 中国北京 · 1K · 文生图",
              "1K / 2K / 4K", "1:8–8:1", "PNG", free="50 张/模型，90 天"),
        image("Wan 2.7 Image Pro（中国区）", True,
              ["text-to-image", "image-edit", "reference"], "¥0.50/输出图", "CNY", .50,
              "cny-premium-1mp", "Wan 2.7 Pro · 中国北京 · 1K · 文生图",
              "1K / 2K（默认）/ 4K", "1:8–8:1", "PNG", free="50 张/模型，90 天"),
        image("Wan 2.7 Image（新加坡）", True,
              ["text-to-image", "image-edit", "reference"], "$0.03/输出图", "USD", .03,
              "usd-standard-1mp", "Wan 2.7 · 新加坡 · 1K · 文生图",
              "1K / 2K / 4K", free="50 张/模型，90 天"),
        image("Wan 2.7 Image Pro（新加坡）", True,
              ["text-to-image", "image-edit", "reference"], "$0.075/输出图", "USD", .075,
              "usd-premium-1mp", "Wan 2.7 Pro · 新加坡 · 1K · 文生图",
              "1K / 2K / 4K", free="50 张/模型，90 天",
              note="区域价来自官方区域表，不使用站内汇率换算。")],
    "zhipu": [
        image("CogView-4（中国区）", True, ["text-to-image"],
              "¥0.06/次；Batch ¥0.03/次", "CNY", .06,
              "cny-standard-1mp", "CogView-4 · 中国区 · 1024×1024 · 非 Batch",
              "边长 512–2048、16 的倍数、面积≤2²¹；含 1024²", formats="URL 保留 30 天"),
        image("CogView-3-Flash（中国区）", True, ["text-to-image"],
              "免费模型；官网未公布数值配额", "CNY",
              basis="独立免费模型，不是 CogView-4 免费额度", resolution="1024×1024",
              free="免费（官网未公布数值配额）",
              note="与 CogView-4 分列，不能把本模型免费状态套用于 CogView-4。"),
        image("CogView-4（全球区）", True, ["text-to-image"],
              "$0.01/张", "USD", .01, "usd-standard-1mp",
              "CogView-4 · 全球区 · 1024×1024", "1024×1024",
              note="全球与中国区为独立官方区域价。")],
    "tencent-hunyuan": [
        image("hy-image-v3（中国区）", True, ["text-to-image", "reference"],
              "¥10/百万 tokens × 20,000 tokens/张 = ¥0.20/张", "CNY", .20,
              "cny-standard-1mp", "TokenHub · 中国区 · 1024×1024",
              "边长 512–2048；总像素≤1024²；含精确 1024²", "37 个预设",
              "PNG / JPG / JPEG；临时 URL", note="当前 TokenHub 未公布稳定数值免费生图额度。"),
        image("hy-image-v3（国际区）", True, ["text-to-image", "reference"],
              "$1.60/百万 tokens × 20,000 tokens/张 = $0.032/张", "USD", .032,
              "usd-standard-1mp", "TokenHub · 国际区 · 1024×1024",
              "1024×1024", note="与中国区是独立官方区域价，不由汇率换算。")],
    "minimax": [
        image("image-01（中国区）", True, ["text-to-image", "reference"],
              "¥0.025/张", "CNY", .025, "cny-standard-1mp",
              "image-01 · 中国区 · 1024×1024", "1024×1024；亦支持 512–2048 自定义",
              "1:1、16:9、4:3、3:2、2:3、3:4、9:16、21:9", "URL（24h）/ Base64"),
        image("image-01（全球区）", True, ["text-to-image", "reference"],
              "$0.0035/张", "USD", .0035, "usd-standard-1mp",
              "image-01 · 全球区 · 1024×1024", "1024×1024",
              note="最新官方全球价；无稳定数值 API 免费额度。")],
    "kling": [image(
        "Kling Image 3.0 / Omni", True,
        ["text-to-image", "image-to-image", "reference"],
        "1K/2K 8 points=¥0.20/张", "CNY", .20,
        "cny-standard-1mp", "Image 3.0 · 中国区 · 1K",
        "1K / 2K / 4K", "多种比例", "URL 保留 30 天",
        note="Omni/O1 最多 10 张参考图；4K 为 ¥0.40/张。美元 UI 等值不作为独立区域价。")],
}


VIDEO = {
    "openai": [
        video("Sora 2", True, ["text-to-video", "image-to-video"],
              "$0.10/生成秒", "USD", .10, "usd-720p-audio",
              "Sora 2 · 720p · 原生音频 · DEPRECATED，2026-09-24 停用",
              "720p", "4/8/12/16/20s", ratios="竖屏 / 横屏", audio=True,
              free="无", note="2026-03-24 起弃用，且官方未给替代型号，不建议新接入。",
               status="sunsetting", sunset="2026-09-24"),
        video("Sora 2 Pro", True, ["text-to-video", "image-to-video"],
              "$0.30/秒（720p）；$0.70/秒（1080p）", "USD", .30,
              "usd-720p-audio", "Sora 2 Pro · 720p · 原生音频 · DEPRECATED",
              "720p", "4/8/12/16/20s", ratios="竖屏 / 横屏", audio=True,
              note="同系列 1080p 为 $0.70/秒；计划 2026-09-24 停用。",
               status="sunsetting", sunset="2026-09-24"),
        video("Sora 2 Pro 1080p", True, ["text-to-video", "image-to-video"],
              "$0.70/生成秒", "USD", .70, "usd-1080p-audio",
              "Sora 2 Pro · 1080p · 原生音频 · DEPRECATED",
              "1080p", "4/8/12/16/20s", ratios="竖屏 / 横屏", audio=True,
              note="计划 2026-09-24 停用。", status="sunsetting",
               sunset="2026-09-24")],
    "google": [
        video("Veo 3.1 Lite", True, ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.05/秒（720p）；$0.08/秒（1080p）", "USD", .05,
              "usd-720p-audio", "Veo 3.1 Lite · 720p · 原生同步音频",
              "720p / 1080p", "4/6/8s", "24fps", "16:9 / 9:16", True,
              "Free Tier 不可用"),
        video("Veo 3.1 Fast", True, ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.10/秒（720p）；$0.12/秒（1080p）", "USD", .10,
              "usd-720p-audio", "Veo 3.1 Fast · 720p · 原生同步音频",
              "720p / 1080p / 4K", "4/6/8s", "24fps", "16:9 / 9:16", True),
        video("Veo 3.1 Standard", True, ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.40/秒（720p/1080p）", "USD", .40,
              "usd-720p-audio", "Veo 3.1 Standard · 720p · 原生同步音频",
              "720p / 1080p / 4K", "4/6/8s", "24fps", "16:9 / 9:16", True),
        video("Veo 3.1 Fast 1080p", True, ["text-to-video", "image-to-video"],
              "$0.12/秒", "USD", .12, "usd-1080p-audio",
              "Veo 3.1 Fast · 1080p · 原生同步音频", "1080p", "4/6/8s", "24fps",
              "16:9 / 9:16", True),
        video("Veo 3.1 Lite 1080p", True, ["text-to-video", "image-to-video"],
              "$0.08/秒", "USD", .08, "usd-1080p-audio",
              "Veo 3.1 Lite · 1080p · 原生同步音频", "1080p", "4/6/8s", "24fps",
              "16:9 / 9:16", True),
        video("Veo 3.1 Standard 1080p", True, ["text-to-video", "image-to-video"],
              "$0.40/秒", "USD", .40, "usd-1080p-audio",
              "Veo 3.1 Standard · 1080p · 原生同步音频", "1080p", "4/6/8s", "24fps",
              "16:9 / 9:16", True)],
    "xai": [video(
        "grok-imagine-video-1.5", True,
        ["text-to-video", "image-to-video", "video-edit", "reference"],
        "$0.080/输出秒", "USD", .08, "usd-720p-audio",
        "Video 1.5 · 720p · 原生音频（官网模型费率）", "480p / 720p / 1080p",
        "1–15s", ratios="1:1、16:9、9:16、4:3、3:4、3:2、2:3", audio=True,
        note="原生音频默认开启，可关闭；型号费率按输出秒列示。")],
    "runway": [video(
        "Gen-4.5", True, ["text-to-video", "image-to-video"],
        "12 credits/秒；1 credit=$0.01", "USD", .12,
        "usd-720p-silent", "Gen-4.5 · 720p · 无原生生成音频",
        "1280×720 / 720×1280 等", "2–10s", "24/25fps", audio=False,
        free="无免费生成 API credits；需充值，最低 $10", note="原生 API 行未公开首尾帧模式。")],
    "adobe": [video(
        "Firefly Video Model 1", True,
        ["text-to-video", "image-to-video", "first-last-frame"],
        "540p 0.4 Operations/秒；720p 1 Op/秒；1080p 2 Ops/秒；USD/Op 未公开",
        basis="企业自定义 Operation 单价", resolution="540p / 720p / 1080p",
        duration="固定 5s", ratios="16:9 / 9:16 / 1:1", audio=None,
        note="需要 Firefly Services entitlement 与 OAuth S2S。")],
    "luma": [
        video("Ray 3.2 SDR 5s 720p", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.30/5s = $0.06/秒", "USD", .06, "usd-720p-silent",
              "Ray 3.2 · SDR · 720p · 5s · 无原生音频", "720p", "5s", "24fps",
              "9:16、3:4、1:1、4:3、16:9、21:9", False, free="0",
              note="10s 总价 $0.90，费率非线性；柱图只比较 5s 档。"),
        video("Ray 3.2 SDR 5s 1080p", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$1.20/5s = $0.24/秒", "USD", .24, "usd-1080p-silent",
              "Ray 3.2 · SDR · 1080p · 5s · 无原生音频", "1080p", "5s", "24fps",
              "9:16、3:4、1:1、4:3、16:9、21:9", False,
              note="旧 Dream Machine/Ray2 API 名称已弃用；当前为 Agents API。")],
    "kling": [
        video("Kling Video 3.0 720p Audio", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.126/秒", "USD", .126, "usd-720p-audio",
              "Video 3.0 · 720p · native audio", "720p", "3–15s",
              ratios="16:9 / 9:16 / 1:1", audio=True),
        video("Kling Video 3.0 720p Silent", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.084/秒", "USD", .084, "usd-720p-silent",
              "Video 3.0 · 720p · audio off", "720p", "3–15s",
              ratios="16:9 / 9:16 / 1:1", audio=False),
        video("Kling Video 3.0 1080p Audio", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.168/秒", "USD", .168, "usd-1080p-audio",
              "Video 3.0 · 1080p · native audio", "1080p", "3–15s",
              ratios="16:9 / 9:16 / 1:1", audio=True),
        video("Kling Video 3.0 1080p Silent", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.112/秒", "USD", .112, "usd-1080p-silent",
              "Video 3.0 · 1080p · audio off", "1080p", "3–15s",
              ratios="16:9 / 9:16 / 1:1", audio=False)],
    "minimax": [video(
        "MiniMax H3", True,
        ["text-to-video", "image-to-video", "first-last-frame", "reference"],
        "$0.08/秒（768p）；$0.13/秒（2K）", "USD", .08,
        basis="768p 与 720p 不同组，故不进柱图", resolution="768p / 2K",
        duration="4–15s", fps="24fps", audio=True,
        note="原生 32kHz 立体声音频；参考视频输入另按同档每秒计费。")],
    "seedance": [video(
        "Doubao Seedance 2.5", True,
        ["text-to-video", "image-to-video", "first-last-frame", "reference", "video-to-video"],
        "按 token×像素动态计费；官方 5s 16:9 无输入示例约 ¥3.36/¥7.56/¥18.71",
        "CNY", basis="480p/720p/1080p 动态 token 价，示例不是固定每秒价",
        resolution="480p / 720p / 1080p", duration="4–30s", fps="24fps",
        ratios="16:9、4:3、1:1、3:4、9:16、21:9 / adaptive",
        audio=True, note="1080p 10-bit；公开促销/账号额度不保证所有账户可用。")],
    "alibaba-wan": [
        video("Wan 3.0（新加坡）", True,
              ["text-to-video", "image-to-video", "first-last-frame", "reference"],
              "$0.10/秒（720p 当前优惠）；$0.20/秒（1080p）", "USD", .10,
              "usd-720p-audio", "Wan 3.0 · 新加坡 · 720p · 原生音频 · 当前 30% 优惠",
              "480p / 720p / 1080p", "2–30s", "30fps", audio=True,
              free="新加坡 30 秒合并输入+输出额度，90 天",
              note="区域与促销价不可同中国人民币价混用。"),
        video("Wan 3.0（中国北京）", True,
              ["text-to-video", "image-to-video", "first-last-frame", "reference"],
              "¥0.60/秒（720p）；¥1.20/秒（1080p）", "CNY", .60,
              "cny-720p-audio", "Wan 3.0 · 中国北京 · 720p · 原生音频",
              "480p / 720p / 1080p", "2–30s", "30fps", audio=True,
              note="Prime 中国区 720p 为 ¥0.90/秒。"),
        video("Wan 3.0 1080p（中国北京）", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "¥1.20/秒", "CNY", 1.20, "cny-1080p-audio",
              "Wan 3.0 · 中国北京 · 1080p · 原生音频",
              "1080p", "2–30s", "30fps", audio=True),
        video("Wan 3.0 1080p（新加坡）", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.20/秒（当前优惠）", "USD", .20, "usd-1080p-audio",
              "Wan 3.0 · 新加坡 · 1080p · 原生音频 · 当前 30% 优惠",
              "1080p", "2–30s", "30fps", audio=True)],
    "vidu": [
        video("Vidu Q3 Turbo 720p", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.055/秒（常规队列）", "USD", .055, "usd-720p-audio",
              "Q3 Turbo · 720p · native audio · regular", "720p", "1–16s", "24fps",
              audio=True, free="无；最低充值 $10", note="off-peak ≤48h 队列为 $0.03/秒。"),
        video("Vidu Q3 Turbo 1080p", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.065/秒（常规队列）", "USD", .065, "usd-1080p-audio",
              "Q3 Turbo · 1080p · native audio · regular", "1080p", "1–16s", "24fps",
              audio=True),
        video("Vidu Q3 Turbo Reference 720p", True, ["reference-to-video"],
              "$0.05/秒（reference2video 常规队列）", "USD", .05,
              "usd-720p-audio", "Q3 Turbo · reference2video · 720p · native audio · regular",
              "720p", "3–16s", "24fps", audio=True,
              note="与普通 T2V/I2V 的 $0.055/秒和 1–16s 口径分开。")],
    "pixverse": [
        video("PixVerse V6 720p Audio", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "12 credits/秒；一次性充值 1 credit=$0.01", "USD", .12,
              "usd-720p-audio", "V6 · 720p · audio on · one-off PAYG pack",
              "720p", "1–15s", audio=True, note="订阅包折算不进入本价格。"),
        video("PixVerse V6 720p Silent", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "9 credits/秒；一次性充值 1 credit=$0.01", "USD", .09,
              "usd-720p-silent", "V6 · 720p · audio off · one-off PAYG pack",
              "720p", "1–15s", audio=False),
        video("PixVerse V6 1080p Audio", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "23 credits/秒；一次性充值 1 credit=$0.01", "USD", .23,
              "usd-1080p-audio", "V6 · 1080p · audio on · one-off PAYG pack",
              "1080p", "1–15s", audio=True),
        video("PixVerse V6 1080p Silent", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "18 credits/秒；一次性充值 1 credit=$0.01", "USD", .18,
              "usd-1080p-silent", "V6 · 1080p · audio off · one-off PAYG pack",
              "1080p", "1–15s", audio=False),
        video("PixVerse V6 Fusion Reference 720p Audio", True, ["reference-to-video"],
              "video_references: 24 credits/秒；1 credit=$0.01", "USD", .24,
              "usd-720p-audio", "V6 Fusion · video_references · 720p · audio on · one-off PAYG",
              "720p", audio=True),
        video("PixVerse V6 Fusion Reference 720p Silent", True, ["reference-to-video"],
              "video_references: 18 credits/秒；1 credit=$0.01", "USD", .18,
              "usd-720p-silent", "V6 Fusion · video_references · 720p · audio off · one-off PAYG",
              "720p", audio=False),
        video("PixVerse V6 Fusion Reference 1080p Audio", True, ["reference-to-video"],
              "video_references: 46 credits/秒；1 credit=$0.01", "USD", .46,
              "usd-1080p-audio", "V6 Fusion · video_references · 1080p · audio on · one-off PAYG",
              "1080p", audio=True),
        video("PixVerse V6 Fusion Reference 1080p Silent", True, ["reference-to-video"],
              "video_references: 36 credits/秒；1 credit=$0.01", "USD", .36,
              "usd-1080p-silent", "V6 Fusion · video_references · 1080p · audio off · one-off PAYG",
              "1080p", audio=False)],
    "pika": [video(
        "Pika 2.5 API", True,
        ["text-to-video", "image-to-video", "first-last-frame"],
        "边际生成价：720p $0.04/秒；1080p $0.06–0.09/秒；另有强制 $10/月",
        "USD", basis="存在固定月费，完整每秒成本无法直接比较",
        resolution="720p / 1080p", duration="T2V 5s；I2V 5/10s；多关键帧 1–10s",
        audio=False, free="0；首月 $10 用量抵扣需先付月费",
        note="2026-08-05 上线的 Pika 第一方 API；Soundtrack 独立计费 $0.005/秒。")],
    "tencent-hunyuan": [video(
        "HY-Video 1.5", True, ["text-to-video", "image-to-video"],
        "预付 1.5 credits/5s；¥1/credit = ¥0.30/秒（720p）", "CNY", .30,
        "cny-720p-silent", "HY-Video 1.5 · 720p · 5s · 预付 · 无原生音频",
        "720p（1080p 文档存在接口支持冲突，未进柱图）", "固定 5s",
        audio=False, free="一次性 50 credits，手动领取，有效 1 年",
        note="后付费 ¥1.2/credit，对应 ¥0.36/秒；无原生音频参数。")],
    "stability": [video(
        "Stable Video Diffusion（self-host only）", False,
        ["image-to-video"], "托管视频 API 已停止；自托管计算成本自定",
        basis="无当前托管 API 价格", resolution="1024×576 等", duration="≤4s",
        fps="模型条件 6fps / 输出 24fps（版本相关）", audio=False,
        note="Stability 托管 SVD API 已于 2025-07-24 停用；当前仅开源自托管。",
         status="self-host-only")],
    "ltx": [
        video("LTX 2.5 Fast 720p", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.09/秒", "USD", .09, "usd-720p-audio",
              "LTX 2.5 Fast · 720p · native audio", "720p", "6–20s",
              "24/25/48/50fps", "横屏 / 竖屏", True, free="无；最低购买 $5"),
        video("LTX 2.5 Fast 1080p", True,
              ["text-to-video", "image-to-video", "first-last-frame"],
              "$0.13/秒", "USD", .13, "usd-1080p-audio",
              "LTX 2.5 Fast · 1080p · native audio", "1080p", "6–20s",
              "24/25/48/50fps", "横屏 / 竖屏", True)],
    "haiper": [video(
        "Haiper Video 2.x", True, ["text-to-video", "image-to-video"],
        "$0.05/秒（720p）；$0.033/秒（540p）", "USD", .05,
        "usd-720p-silent", "Haiper 2.x · 720p · 无原生音频",
        "540p / 720p", "4/6s", "24fps", audio=False, free="无")],
    "aws-nova": [video(
        "Amazon Nova Reel 1.1", True, ["text-to-video", "image-to-video"],
        "$0.08/秒", "USD", .08, "usd-720p-silent",
        "Nova Reel 1.1 · 1280×720 · 无音频 · LEGACY/EOL 2026-09-30",
        "1280×720", "6s 增量，最长 120s", "24fps", audio=False,
        free="无 Nova Reel 免费额度", note="2026-03-30 起 Legacy，仅存量客户；连续 15 天无成功调用可能失去访问；2026-09-30 EOL。",
         status="legacy-existing-only", sunset="2026-09-30")],
}



# Provider/model-level access facts inherited by every split output variant.
for _item in VIDEO["google"]:
    _item["free_quota"] = "Free Tier 不可用"
for _item in VIDEO["openai"]:
    _item["free_quota"] = "无"
for _item in VIDEO["luma"]:
    _item["free_quota"] = "0"
for _item in VIDEO["ltx"]:
    _item["free_quota"] = "无；最低购买 $5"
for _item in VIDEO["vidu"]:
    _item["free_quota"] = "无；最低充值 $10"

REGION_OVERRIDES = {
    "imagegen": {
        "Seedream 5.0 Lite": "domestic", "Seedream 5.0 Pro（中国区）": "domestic",
        "Seedream 5.0 Pro（BytePlus）": "intl",
        "Wan 2.7 Image（中国区）": "domestic", "Wan 2.7 Image Pro（中国区）": "domestic",
        "Wan 2.7 Image（新加坡）": "intl", "Wan 2.7 Image Pro（新加坡）": "intl",
        "CogView-4（中国区）": "domestic", "CogView-4（全球区）": "intl",
        "hy-image-v3（中国区）": "domestic", "hy-image-v3（国际区）": "intl",
        "image-01（中国区）": "domestic", "image-01（全球区）": "intl",
    },
    "videogen": {
        "Wan 3.0（新加坡）": "intl", "Wan 3.0（中国北京）": "domestic",
        "Wan 3.0 1080p（中国北京）": "domestic", "Wan 3.0 1080p（新加坡）": "intl",
    },
}


IMAGE_COMPARISON_FACTS = {'grok-imagine-image-2.0': (1024, 1024, 'standard'), 'Stable Image Ultra': (1024, 1024, 'premium'), 'Ideogram 4 Default': (1024, 1024, 'standard'), 'Ideogram 4 Quality': (1024, 1024, 'premium'), 'Recraft V4.1 Raster': (1024, 1024, 'standard'), 'Seedream 5.0 Pro（中国区）': (1024, 1024, 'premium'), 'Seedream 5.0 Pro（BytePlus）': (1024, 1024, 'premium'), 'Wan 2.7 Image（中国区）': (1024, 1024, 'standard'), 'Wan 2.7 Image Pro（中国区）': (1024, 1024, 'premium'), 'Wan 2.7 Image（新加坡）': (1024, 1024, 'standard'), 'Wan 2.7 Image Pro（新加坡）': (1024, 1024, 'premium'), 'CogView-4（中国区）': (1024, 1024, 'standard'), 'CogView-4（全球区）': (1024, 1024, 'standard'), 'hy-image-v3（中国区）': (1024, 1024, 'standard'), 'hy-image-v3（国际区）': (1024, 1024, 'standard'), 'image-01（中国区）': (1024, 1024, 'standard'), 'image-01（全球区）': (1024, 1024, 'standard'), 'Kling Image 3.0 / Omni': (1024, 1024, 'standard')}
VIDEO_COMPARISON_FACTS = {'Sora 2': '720p', 'Sora 2 Pro': '720p', 'Sora 2 Pro 1080p': '1080p', 'Veo 3.1 Lite': '720p', 'Veo 3.1 Fast': '720p', 'Veo 3.1 Standard': '720p', 'Veo 3.1 Fast 1080p': '1080p', 'Veo 3.1 Lite 1080p': '1080p', 'Veo 3.1 Standard 1080p': '1080p', 'grok-imagine-video-1.5': '720p', 'Gen-4.5': '720p', 'Ray 3.2 SDR 5s 720p': '720p', 'Ray 3.2 SDR 5s 1080p': '1080p', 'Kling Video 3.0 720p Audio': '720p', 'Kling Video 3.0 720p Silent': '720p', 'Kling Video 3.0 1080p Audio': '1080p', 'Kling Video 3.0 1080p Silent': '1080p', 'Wan 3.0（新加坡）': '720p', 'Wan 3.0（中国北京）': '720p', 'Wan 3.0 1080p（中国北京）': '1080p', 'Wan 3.0 1080p（新加坡）': '1080p', 'Vidu Q3 Turbo 720p': '720p', 'Vidu Q3 Turbo 1080p': '1080p', 'Vidu Q3 Turbo Reference 720p': '720p', 'PixVerse V6 720p Audio': '720p', 'PixVerse V6 720p Silent': '720p', 'PixVerse V6 1080p Audio': '1080p', 'PixVerse V6 1080p Silent': '1080p', 'PixVerse V6 Fusion Reference 720p Audio': '720p', 'PixVerse V6 Fusion Reference 720p Silent': '720p', 'PixVerse V6 Fusion Reference 1080p Audio': '1080p', 'PixVerse V6 Fusion Reference 1080p Silent': '1080p', 'HY-Video 1.5': '720p', 'LTX 2.5 Fast 720p': '720p', 'LTX 2.5 Fast 1080p': '1080p', 'Haiper Video 2.x': '720p', 'Amazon Nova Reel 1.1': '720p'}


def _apply_seed_facts():
    for offerings in IMAGE.values():
        for offering in offerings:
            facts = IMAGE_COMPARISON_FACTS.get(offering["name"])
            if offering.get("comparison_group") and not facts:
                raise RuntimeError(f"missing independent image comparison facts: {offering['name']}")
            if facts:
                offering["comparison_width"], offering["comparison_height"], offering["quality_tier"] = facts
            region = REGION_OVERRIDES["imagegen"].get(offering["name"])
            if region:
                offering["region"] = region
    for offerings in VIDEO.values():
        for offering in offerings:
            resolution = VIDEO_COMPARISON_FACTS.get(offering["name"])
            if offering.get("comparison_group") and not resolution:
                raise RuntimeError(f"missing independent video comparison facts: {offering['name']}")
            if resolution:
                offering["comparison_resolution"] = resolution
            region = REGION_OVERRIDES["videogen"].get(offering["name"])
            if region:
                offering["region"] = region


_apply_seed_facts()


VERIFIED_AT = "2026-09-08"
VERIFIED_MANIFEST = {'imagegen': {'adobe': '96707f0740478660c53f72ef3991682c1a22a4ef5ff54af573e4359f4e129f2e',
              'alibaba-wan': '6555e5cfbddc1b413597edc0f268de590352486ffe2c96c38252e91510110a02',
              'bfl': '785d2822520d8cc3711e2e5a79bd1dc5d72d12d28b4d202e4473f3de7220dec3',
              'google': 'f1658780511b301326d107b1625d894ebd2bcbb6dd75143b80d5be3a03155322',
              'ideogram': '82109c18a37b6dea82a60d3fc7943b7081ea4a65a37f7583c8c87c099344e1bb',
              'kling': 'e54512de3833a7f3acd0ae41e01c21a624ef7980dad6f2972d00cc2ddc2c3f3d',
              'leonardo': '805de617022e12acfdaec0c6669cfa83ae2cbf290f9b22d546e805cb650b0dd5',
              'midjourney': 'd17905d1d3942b5d8a66103aa233dedfeb66b5e551ad65029f743418e730c7e8',
              'minimax': '2e329251ea070a236ef2c0caf28d4296b3a6c1451c7ae6fa0a12bfb5a746601d',
              'openai': '7ae33a7486f4099fa6f5329fb885b5109917777bbdbc7fbcc5d61f5ab3cc5bfe',
              'recraft': 'cc8d4dede8e3a8980d324f46211ed863c69c025bef879516c08df571d587bdd5',
              'seedream': '77723ed963ac917f4595776445ec29032e17d349c969eb29247dde45f55d5508',
              'stability': '9dd67501aba856e70c51c30e7087f4886dd9248fdd6ff5b12a89c6b571de42e0',
              'tencent-hunyuan': '74f5e6a695d9620ebbae2f4ba0ae82e3ca6c25dacb6e798537da2d9f9cb46094',
              'xai': 'dc86ed1743bc4390cf82181fee32ccd287b31818eb7d8a5b4853b10b0351ccfa',
              'zhipu': 'cc9491e17d4899100f84c4f59d492b0ad19002f17817e9f25763a6de7b786d25'},
 'videogen': {'adobe': '8bef91e10939141ead5292ce7dbdf12933e1e4a924bff759d943de291a335bcd',
              'alibaba-wan': 'f7829eee0690bb68e29e407ab262452ac333d10acfd13d60b65f5b1cf121313b',
              'aws-nova': 'f224782d33433f699fc97125c273b2321a1e13d0d694a61604c010e45c06860b',
              'google': '9741c20854ade3557f9ed2615b19e07dd38fd9ae2a84d6230b32e0ccf89725dc',
              'haiper': '78d51d7a4d46214373c533165b6dfce16130744dc18a003670535391a585f697',
              'kling': '5c4559a1b866fac3808ef8992a7813efa3d2c504ec8fc0b555a33f5f9d2cce04',
              'ltx': 'f842c082ac1b7fa2148cd616ee20ffc228b66692b04a09ffbb5f0be545f4cf17',
              'luma': '21b89fa82ba83b11bd98b60290b09d198ecb6107b97af87e633f2af7ad668c28',
              'minimax': 'e8c9c794240235cce2545ee22a6832dbf157e6ba51e42df45f099fb7989f92aa',
              'openai': '9c6a7db42d8cd425319551abbe45fbc6cdb5449d49bd1b25052ff864df38fbb0',
              'pika': 'b6fc8fb2a9c2eadd29dc3110e7a30a2caae4abd5f0663f6d8fb9dc3328396955',
              'pixverse': '0a9780a1664d68d4069b6792529eaf27bf55b249954a9d920c0fc1c0d6b51e87',
              'runway': '7c5f3a92d21db75cf509109e39221b2353aeb416a4f4f339c95324508c6c1891',
              'seedance': 'e0b687133ab1bd51bbc8fc16bf8ce7bb6c4f3a25a4b5e8ea78ef7618b140b7b1',
              'stability': '75fae5083114443e85310dd9daca7e80847be61ff273cb639de17cd3b47c3762',
              'tencent-hunyuan': '6cb8f15de577da7275445fac4925f839f6a9c3eb086e38bb12ba15ce34cf7d0a',
              'vidu': '357c04e03f0b26c9c9560d56389e0d6679788dd947209126e79bc6569ed209c7',
              'xai': 'cecbf8aa973a313aecea7b7d53ea3a1841962e4a494361911f67defd991fb20d'}}


def verification_hash(kind, cfg, offerings):
    sources = cfg.get(f"{kind}_urls") or [cfg[f"{kind}_url"]]
    payload = {"offerings": offerings, "source_urls": sources,
               "examples": cfg.get("examples") or []}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_catalog(filename):
    raw = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8")) or {}
    return raw.get("providers") or []


def _atomic_write_json(path: Path, record: dict) -> None:
    payload = json.dumps(
        record, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        os.fchmod(fd, old_mode | 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_catalog(filename, key, facts, outdir, force):
    configs = load_catalog(filename)
    ids = {item["id"] for item in configs}
    missing = ids - facts.keys()
    extra = facts.keys() - ids
    if missing or extra:
        raise SystemExit(f"{filename}: facts mismatch, missing={sorted(missing)}, extra={sorted(extra)}")
    target = ROOT / "data" / outdir
    target.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = skipped = 0
    for cfg in configs:
        path = target / f'{cfg["id"]}.json'
        if path.exists() and not force:
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("source") == "official":
                skipped += 1
                continue
        urls = cfg.get(f"{key}_urls") or []
        one = cfg.get(f"{key}_url")
        if one:
            urls = [one, *urls]
        offerings = facts[cfg["id"]]
        actual_hash = verification_hash(key, cfg, offerings)
        expected_hash = VERIFIED_MANIFEST.get(key, {}).get(cfg["id"])
        if not expected_hash or actual_hash != expected_hash:
            raise RuntimeError(
                f"{key}/{cfg['id']}: seed facts changed without reviewed manifest hash")
        record = {
            "provider_name": cfg.get("name_cn") or cfg.get("name") or cfg["id"],
            "category": cfg.get("category"),
            "source": "official_seed",
            "seed_verified": True,
            "verified_at": VERIFIED_AT,
            "verification_hash": actual_hash,
            "page_has_relevant_content": None,
            "product_status": "active",
            "product_status_date": None,
            "product_status_note": None,
            "seed_cutoff": CUTOFF,
            "source_url": urls[0] if urls else None,
            "source_urls": urls,
            "official_examples": cfg.get("examples") or [],
            "service_regions": sorted({item["region"] for item in offerings
                                       if item.get("region") in ("intl", "domestic")}),
            "offerings": offerings,
            "fetched_at": None,
            "seeded_at": now,
        }
        if key == "imagegen":
            record["has_image_generation"] = bool(offerings)
        else:
            record["has_video_generation"] = bool(offerings)
        _atomic_write_json(path, record)
        written += 1
    print(f"{key}: wrote {written}, preserved official {skipped}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="also replace automatic official records")
    args = parser.parse_args()
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if os.environ.get("MODEL_PRICE_LOCK_HELD") == "1":
        write_catalog("imagegen.yaml", "imagegen", IMAGE, "imagegen", args.force)
        write_catalog("videogen.yaml", "videogen", VIDEO, "videogen", args.force)
        return
    with (data_dir / ".scraper.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("已有抓取/构建任务持有 data/.scraper.lock，种子未改写") from exc
        try:
            write_catalog("imagegen.yaml", "imagegen", IMAGE, "imagegen", args.force)
            write_catalog("videogen.yaml", "videogen", VIDEO, "videogen", args.force)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
