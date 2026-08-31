"""生成/刷新种子数据 data/providers/*.json + data/news/*.json + 变动流水。

种子数据是对各厂商官网页面的手工抓取整理(2026-08-31 从各官网刷新,
抓取脚本 /tmp/fetch_fresh.py), 让站点在配置 OPENAI_API_KEY(自动
抽取)之前不至于空白; source 标为 "seed", 首次 Claude 抽取成功后自动
替换(站点上显示「种子数据 · 待校准」徽标)。
用法: .venv/bin/python3 scripts/make_seed.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.history import (CHANGES_FILE, DATA, NEWS_DIR,  # noqa: E402
                             PROVIDERS_DIR, _atomic_write_json)

NOW = "2026-08-31T07:10:00Z"
SEED = "seed"


def provider(pid, currency, models, url=None, promotions=None,
             note_extra=None, page_has_pricing=True, fetched=NOW):
    rec = {
        "url": url,
        "source": SEED,
        "currency": currency,
        "promotions": promotions,
        "models": [
            {"model": m[0], "input_per_1m": m[1], "output_per_1m": m[2],
             "cached_input_per_1m": m[3] if len(m) > 3 else None,
             "currency": None, "note": m[4] if len(m) > 4 else None}
            for m in models
        ],
        "page_has_pricing": page_has_pricing,
        "price_hash": None,
        "fetched_at": fetched,
        "status_note": note_extra,
    }
    _atomic_write_json(PROVIDERS_DIR / f"{pid}.json", rec)


def news(pid, entries):
    _atomic_write_json(NEWS_DIR / f"{pid}.json", {
        "entries": [{"date": d, "title": t, "url": u, "summary": s,
                     "first_seen": NOW}
                    for d, t, u, s in entries],
        "news_hash": None, "fetched_at": None,
    })


# ---------------- 国际(2026-08-31 复核, 价格与 08-27 一致) ----------------

provider("anthropic", "USD", [
    ("claude-fable-5", 10, 50, None, "最强旗舰"),
    ("claude-opus-5", 5, 25, None, None),
    ("claude-opus-4-8", 5, 25, None, "旧代, 仍可用"),
    ("claude-opus-4-6", 5, 25, None, "旧代, 仍可用"),
    ("claude-sonnet-5", 2, 10, None, None),
    ("claude-sonnet-4-6", 3, 15, None, "旧代, 仍可用"),
    ("claude-haiku-4-5", 1, 5, None, None),
], url="https://platform.claude.com/docs/en/about-claude/models/overview.md")

provider("openai", "USD", [
    ("gpt-5.6-sol", 4, 20, 0.4, None),
    ("gpt-5.6-terra", 2, 12, 0.2, None),
    ("gpt-5.6-luna", 0.2, 1.2, 0.02, None),
    ("gpt-5.5", 5, 30, 0.5, "≤272K 上下文"),
    ("gpt-5.5-pro", 30, 180, None, "≤272K 上下文"),
    ("gpt-5.4", 2.5, 15, 0.25, "≤272K 上下文"),
    ("gpt-5.4-mini", 0.75, 4.5, 0.075, None),
    ("gpt-5.4-nano", 0.2, 1.25, 0.02, None),
    ("gpt-5.4-pro", 30, 180, None, "≤272K 上下文"),
    ("gpt-5.2", 1.75, 14, 0.175, None),
    ("gpt-5.2-pro", 21, 168, None, None),
    ("gpt-5.1", 1.25, 10, 0.125, None),
    ("gpt-5", 1.25, 10, 0.125, None),
    ("gpt-5-mini", 0.25, 2, 0.025, None),
    ("gpt-5-nano", 0.05, 0.4, 0.005, None),
    ("gpt-5-pro", 15, 120, None, None),
    ("gpt-4.1", 2, 8, 0.5, None),
    ("gpt-4.1-mini", 0.4, 1.6, 0.1, None),
    ("gpt-4.1-nano", 0.1, 0.4, 0.025, None),
    ("gpt-4o", 2.5, 10, 1.25, None),
    ("gpt-4o-mini", 0.15, 0.6, 0.075, None),
    ("o3", 2, 8, 0.5, None),
    ("o4-mini", 1.1, 4.4, 0.275, None),
], url="https://platform.openai.com/docs/pricing")

provider("google", "USD", [
    ("gemini-3.7-flash", 0.75, 3.75, 0.075,
     "发布期优惠价, 2027-01-01 起涨至 $1.50/$7.50"),
    ("gemini-3.6-flash", 0.75, 3.75, 0.075,
     "发布期优惠价, 2027-01-01 起涨至 $1.50/$7.50"),
    ("gemini-3.5-flash", 1.5, 9, 0.15, None),
    ("gemini-3.5-flash-lite", 0.3, 2.5, 0.03, None),
    ("gemini-3.1-flash-lite", 0.25, 1.5, 0.025, "音频输入 $0.50"),
    ("gemini-3.1-pro-preview", 2, 12, 0.2, "≤200K; >200K 输入 $4 / 输出 $18"),
    ("gemini-2.5-pro", 1.25, 10, 0.125, "≤200K; >200K 输入 $2.50 / 输出 $15"),
    ("gemini-2.5-flash", 0.3, 2.5, 0.03, "音频输入 $1.00"),
    ("gemini-2.5-flash-lite", 0.1, 0.4, 0.01, "音频输入 $0.30"),
], url="https://ai.google.dev/gemini-api/docs/pricing")

provider("xai", None, [], url="https://docs.x.ai/docs/models",
         note_extra="本地网络无法访问该站点, 等待 CI 首次抓取",
         page_has_pricing=False, fetched=None)
provider("mistral", None, [], url="https://mistral.ai/pricing",
         note_extra="本地网络无法访问该站点, 等待 CI 首次抓取",
         page_has_pricing=False, fetched=None)

# ---------------- 国内(2026-08-31 从官网刷新) ----------------

provider("deepseek", "CNY", [
    ("deepseek-v4-flash", 3.0, 9.0, 0.1, "高峰时段价; 空闲时段半价"),
    ("deepseek-v4-pro", 9.0, 27.0, 0.3, "高峰时段价; 空闲时段半价"),
    ("deepseek-v4-flash-vision-exp", 3.0, 9.0, 0.1,
     "实验性视觉理解模型, 2026-08-21 上线; 价格同 v4-flash"),
], url="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
   promotions="空闲时段(工作日 9-12 / 14-18 点以外)所有价格五折; 上下文缓存命中价再低一个数量级。")

provider("qwen", "CNY", [
    ("qwen3.8-max-prime", 24, 72, None, "优速模式"),
    ("qwen3.8-max", 12, 36, None, "Batch 调用半价"),
    ("qwen3.7-max", 6, 18, None, "限时5折, 原价 ¥12/¥36"),
    ("qwen3.7-plus", 2, 8, None, "≤256K; 256K-1M 档 ¥6/¥24"),
    ("qwen3.7-flash", 0.2, 0.8, None, "阶梯计价, 长上下文更高"),
    ("qwen3.5-plus", 0.8, 4.8, None, None),
], url="https://help.aliyun.com/zh/model-studio/model-pricing.md",
   promotions="新人免费额度(每模型100万Token, 90天有效); Batch 调用半价; 上下文缓存享折扣; 部分模型限时折扣。")

provider("doubao", "CNY", [
    ("doubao-seed-2.1-pro", 6.0, 30.0, 1.2,
     "新一代旗舰; 输入长度 [0,256]K 档; 批量推理 ¥3/¥15"),
    ("doubao-seed-2.1-turbo", 3.0, 15.0, 0.6,
     "输入长度 [0,256]K 档; 批量推理 ¥1.5/¥7.5"),
    ("deepseek-v4-pro", 9.0, 27.0, 0.3,
     "2026-08-28 调价生效(原 ¥4.5/¥13.5); 批量推理 ¥4.5/¥13.5"),
    ("deepseek-v4-flash", 3.0, 9.0, 0.1, "批量推理 ¥1.5/¥4.5"),
    ("doubao-seed-2.0-lite", 0.48, 5.76, 0.48, "阶梯计价(≤32K 档)"),
    ("doubao-seed-2.0-mini", 0.48, 5.76, 0.48, "阶梯计价(≤32K 档)"),
], url="https://www.volcengine.com/docs/82379/1099320",
   promotions="deepseek-v4-pro 已于 2026-08-28 起调价(输入 ¥4.5→¥9, 输出 ¥13.5→¥27); "
              "新一代 doubao-seed-2.1 系列(pro / turbo)上线; 批量推理为在线价五折。")

provider("zhipu", "CNY", [
    ("GLM-5.3", 8, 28, 2, "新品"),
    ("GLM-5.3-Flash", 0.4, 1.4, 0.115,
     "5折限时两周, 原价 ¥0.8/¥2.8; 缓存存储限时免费"),
    ("GLM-5.2", 8, 28, 2, None),
    ("GLM-5.1", 6, 24, 1.3, "输入 [0,32)K 档; [32+K) 档 ¥8/¥28"),
    ("GLM-5", 4, 18, 1, "输入 [0,32)K 档; [32+K) 档 ¥6/¥22"),
], url="https://open.bigmodel.cn/pricing",
   promotions="GLM-5.3-Flash 限时五折(两周); 多个模型缓存存储限时免费。")

provider("moonshot", "CNY", [
    ("kimi-k3", 20, 100, 2, "旗舰, 1M 上下文"),
    ("kimi-k2.7-code", 6.5, 27, 1.3, "Coding 模型"),
    ("kimi-k2.7-code-highspeed", 13, 54, 2.6, "Coding 高速版"),
    ("kimi-k2.6", 6.5, 27, 1.1, None),
    ("kimi-k2.5", 4, 21, 0.7, "视觉+文本多模态"),
    ("moonshot-v1-8k", 2, 10, None, "经典系列, 预计 8月31日全平台下线"),
    ("moonshot-v1-32k", 5, 20, None, "经典系列, 预计 8月31日全平台下线"),
    ("moonshot-v1-128k", 10, 30, None, "经典系列, 预计 8月31日全平台下线"),
], url="https://platform.kimi.com/docs/pricing/chat.md",
   promotions="文件内容抽取/文件存储接口限时免费; moonshot-v1 系列预计 8 月 31 日全平台下线。")

provider("minimax", "CNY", [
    ("MiniMax-M3", 2.1, 8.4, 0.42,
     "≤512K 输入; 永久五折, 原价 ¥4.2/¥16.8"),
    ("MiniMax-M3 (>512K)", 4.2, 16.8, 0.84,
     ">512K 长输入档; 永久五折, 原价 ¥8.4/¥33.6"),
    ("MiniMax-M2.7", 2.1, 8.4, 0.42, "缓存写入 ¥2.625"),
    ("MiniMax-M2.7-highspeed", 4.2, 16.8, 0.42,
     "高速版; 缓存写入 ¥2.625"),
], url="https://platform.minimaxi.com/docs/guides/pricing-paygo.md",
   promotions="MiniMax-M3 按量价格永久五折; 优先档为标准价 1.5 倍; 另有 Token Plan 订阅。")

# ---------------- 公告种子(取自官网公告页) ----------------

news("deepseek", [
    ("2026-08-21", "DeepSeek-V4-Flash-Vision-Exp 发布",
     "https://api-docs.deepseek.com/zh-cn/updates",
     "实验性多模态视觉理解模型上线, model=deepseek-v4-flash-vision-exp, 价格与 v4-flash 相同。"),
    ("2026-08-13", "DeepSeek-V4-Pro 更新",
     "https://api-docs.deepseek.com/zh-cn/updates",
     "V4-Pro 正式版同步在 APP、网页端和 API 上线, 调用方式不变。"),
    ("2026-07-31", "DeepSeek-V4-Flash 更新",
     "https://api-docs.deepseek.com/zh-cn/updates",
     "V4-Flash 正式版 API 上线公测。"),
])

news("zhipu", [
    ("2026-08-26", "GLM-5.3-Flash 原生多模态模型上线",
     "https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash",
     "原生视觉能力, 总参320B激活18B混合架构, 支持代码/浏览器/GUI 协同闭环。"),
    ("2026-08-19", "GLM-5.3 新一代旗舰模型上线",
     "https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3",
     "编程能力较 GLM-5.2 提升 50%, 网络安全能力持平 Mythos 5。"),
    ("2026-06-16", "GLM-5.2 新一代旗舰模型上线",
     "https://docs.bigmodel.cn/cn/guide/models/text/glm-5.2",
     "支持 1M 无损上下文, Coding 与长程任务开源 SOTA。"),
    ("2026-04-07", "GLM-5.1 新一代旗舰模型上线",
     "https://docs.bigmodel.cn/cn/guide/models/text/glm-5.1",
     "长程任务一次可持续工作 8 小时, 综合能力对齐 Claude Opus 4.6。"),
    ("2026-02-12", "GLM-5 新一代旗舰模型上线",
     "https://docs.bigmodel.cn/cn/guide/models/text/glm-5",
     "新一代旗舰模型。"),
])

news("minimax", [
    ("2026-07-31", "MiniMax H3 多模态视频模型发布",
     "https://www.minimaxi.com/blog/minimax-h3",
     "新一代开放通用多模态视频模型, 文本/图像/视频/声音统一上下文。"),
    ("2026-06-01", "MiniMax M3 语言模型正式发布",
     "https://www.minimaxi.com/models/text/m3",
     "面向 Agent 推理、工具调用、代码与长上下文任务。"),
    ("2026-03-18", "MiniMax M2.7 系列发布",
     "https://www.minimaxi.com/news/minimax-m27-zh",
     "M2.7 / M2.7-highspeed 正式发布, 开启模型自我迭代。"),
    ("2026-02", "MiniMax M2.5 系列发布",
     "https://www.minimaxi.com/news/minimax-m25",
     "编程、工具调用、办公生产力场景达到或刷新行业 SOTA。"),
])

news("qwen", [
    ("2026-08", "模型升级通知与 Token Plan 权益升级",
     "https://help.aliyun.com/zh/model-studio/model-release-notes.md",
     "百炼平台功能动态: 模型升级通知、个人版 Token Plan 用户权益升级。"),
    (None, "阿里云百炼部分模型上下文缓存降价通知",
     "https://www.aliyun.com/notice/117497", None),
    (None, "Qwen3-Coder-Plus 限时优惠",
     "https://help.aliyun.com/document_detail/2949810.html", None),
    (None, "通义千问VL系列模型降价通知",
     "https://help.aliyun.com/document_detail/2864425.html", None),
    (None, "千问系列模型降价通知",
     "https://help.aliyun.com/document_detail/2849941.html", None),
])

# ---------------- 变动流水(真实市场事件, 手工记录) ----------------

CHANGES = [
    ("doubao", "火山方舟 (豆包)", "change", "deepseek-v4-pro",
     [("input_per_1m", 4.5, 9.0), ("output_per_1m", 13.5, 27.0)]),
    ("doubao", "火山方舟 (豆包)", "new", "doubao-seed-2.1-pro",
     [("input_per_1m", None, 6.0), ("output_per_1m", None, 30.0)]),
    ("doubao", "火山方舟 (豆包)", "new", "doubao-seed-2.1-turbo",
     [("input_per_1m", None, 3.0), ("output_per_1m", None, 15.0)]),
    ("deepseek", "DeepSeek", "new", "deepseek-v4-flash-vision-exp",
     [("input_per_1m", None, 3.0), ("output_per_1m", None, 9.0)]),
]

try:
    changes = json.loads(CHANGES_FILE.read_text(encoding="utf-8"))
except Exception:
    changes = []
known = {(c.get("provider"), c.get("model"), c.get("kind")) for c in changes}
for prov, pname, kind, model, fields in CHANGES:
    if (prov, model, kind) in known:
        continue
    changes.append({"ts": NOW, "provider": prov, "provider_name": pname,
                    "kind": kind, "model": model,
                    "fields": [{"field": f, "old": o, "new": n}
                               for f, o, n in fields]})
_atomic_write_json(CHANGES_FILE, changes)

# ---------------- 汇率 + 元信息 ----------------

_atomic_write_json(DATA / "meta.json", {
    "fx": {"usd_cny": 6.7209, "source": "frankfurter.app",
           "fetched_at": NOW},
    "generated_at": NOW,
})

print("种子数据已写入", DATA)
for p in sorted(PROVIDERS_DIR.glob("*.json")):
    print("  provider:", p.name)
for p in sorted(NEWS_DIR.glob("*.json")):
    print("  news:    ", p.name)
print("  changes: ", len(changes), "条")
