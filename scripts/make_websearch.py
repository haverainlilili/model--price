"""生成联网搜索比较的官网事实种子数据。

数据只取官网明确陈述的字段；可比较柱价仅在官网能直接或机械换算成
USD/1000 次请求时填写。首次自动抓取成功后，同一文件会由官网抽取结果更新。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scraper.history import WEBSEARCH_DIR, _atomic_write_json  # noqa: E402


def offering(name, pricing=None, price=None, basis=None, free=None,
             output=None, cites=None, default=False, note=None):
    return {
        "name": name,
        "pricing": pricing,
        "price_per_1k_usd": price,
        "price_basis": basis,
        "free_quota": free,
        "output_type": output,
        "cites_sources": cites,
        "default_on": default,
        "note": note,
    }


VERIFIED_SEED = {
    "anthropic", "openai", "google", "tavily", "exa", "brave",
    "perplexity", "you", "linkup", "jina", "firecrawl", "bocha",
    "serper", "serpapi", "google-cse", "dataforseo", "brightdata",
}


SEED = {
    "anthropic": offering(
        "Claude API Web search tool",
        "$10 / 1,000 searches，另加标准 token 费用", 10,
        "Web search tool call", None, "模型内置工具；带引用答案", True, False,
        "搜索结果内容计入输入 token；Messages Batches API 同价"),
    "openai": offering(
        "OpenAI Web search tool",
        "$10 / 1,000 calls，搜索内容 token 按模型费率计费", 10,
        "Web search（all models）", None, "模型内置工具；带引用答案", True, False,
        "非推理模型的 preview 档为 $25/1k；正式 web search 为 $10/1k"),
    "google": offering(
        "Grounding with Google Search",
        "每月 5,000 次免费，之后 $14 / 1,000 search requests", 14,
        "Gemini 3.x，超出免费额度", "5,000 search requests/月（Gemini 3.x 共享）",
        "模型内置 Grounding；返回来源引用", True, False,
        "按模型实际执行的每个 Google Search query 计费"),
    "xai": offering(
        "Grok 联网搜索 / Search tools", None, None, None, None,
        "模型内置搜索工具", None, None, "等待官网自动抓取补全价格与来源字段"),
    "mistral": offering(
        "Mistral Web search", None, None, None, None,
        "模型内置搜索工具", None, None, "等待官网自动抓取补全价格与来源字段"),
    "deepseek": offering(
        "DeepSeek 联网搜索", None, None, None, None,
        "模型工具（待官网确认）", None, None, "官网公开 API 信息待自动抓取确认"),
    "qwen": offering(
        "百炼联网搜索", None, None, None, None,
        "模型内置搜索增强", None, None, "官网价格与引用字段待自动抓取补全"),
    "doubao": offering(
        "火山方舟联网搜索", None, None, None, None,
        "模型内置搜索增强", None, None, "官网价格与引用字段待自动抓取补全"),
    "zhipu": offering(
        "GLM web_search", None, None, None, None,
        "模型内置搜索工具", None, None, "官网价格与引用字段待自动抓取补全"),
    "moonshot": offering(
        "Kimi 联网搜索", None, None, None, None,
        "模型内置搜索工具", None, None, "官网价格与引用字段待自动抓取补全"),
    "minimax": offering(
        "MiniMax 联网搜索", None, None, None, None,
        "模型工具（待官网确认）", None, None, "官网公开 API 信息待自动抓取确认"),

    "tavily": offering(
        "Tavily Search API",
        "免费 1,000 credits/月；PAYG $0.008/credit；Basic 1 credit/次", 8,
        "PAYG Basic（1 credit/request）", "1,000 credits/月",
        "结构化搜索结果，可选正文/答案", True, False,
        "Advanced 每次 2 credits；月付档为 $0.005–$0.0075/credit"),
    "exa": offering(
        "Exa Search API",
        "/search $7 / 1,000 requests（含最多 10 个结果）", 7,
        "/search 基础价（≤10 results）", "$20 注册额度；每月 $10 free credits",
        "语义/实时结构化结果；另有 Answer API", True, False,
        "超过 10 个结果另收 $1/1k results；页面摘要 $1/1k pages"),
    "brave": offering(
        "Brave Search API",
        "$5 / 1,000 requests", 5, "Web Search API", "$5 每月免费 credits",
        "独立索引的 Web/News/Image 结构化结果", True, False,
        "官网同时提供面向 LLM 的 Context endpoint"),
    "perplexity": offering(
        "Perplexity Search API",
        "$5 / 1,000 successful requests；无额外 token 费", 5,
        "Search API", None, "结构化 results[]（title/url/snippet）", True, False,
        "一次成功请求最多可含 5 个 query，仍按一个 billing unit 计费"),
    "you": offering(
        "You.com Web Search API",
        "$5 / 1,000 calls；每次可返回 1–100 个结果", 5,
        "Web Search API", "$100 启动 credits",
        "结构化搜索结果；另有带引用 Research API", True, False,
        "启用 livecrawl 时另加 $0.001/结果"),
    "linkup": offering(
        "Linkup Search API",
        "Standard $0.005–$0.006/request", 5,
        "Standard raw results（$0.005/request）", "4,000 queries free",
        "结构化结果或带来源答案", True, False,
        "sourced/structured 为 $6/1k；Deep 为 $50–$55/1k"),
    "jina": offering(
        "Jina Search API (s.jina.ai)",
        "$0.050 / 1M tokens；每次搜索固定从 10,000 tokens 起", None,
        "按 token 计费，不进入千次请求柱图", "新 API key 含 10M tokens（最多约 1,000 次最低用量搜索）",
        "5 条结构化结果并转为 LLM-friendly text", True, False,
        "实际成本随 token budget 变化，不能与固定请求价直接比较"),
    "firecrawl": offering(
        "Firecrawl Search",
        "Search 2 credits/10 results；Hobby PAYG $5/1,000 credits", 10,
        "Hobby PAYG，10 results（2 credits）", "1,000 credits/月（约 500 次 1–10 结果搜索）",
        "搜索结果，可选抓取正文", True, False,
        "不同付费档每 credit 单价不同；抓取结果正文另耗 credits"),

    "bocha": offering(
        "博查 Web Search API",
        None, None, None, None,
        "结构化 Web 搜索结果（URL/snippet/summary）", True, False,
        "支持自然语言、多模态混合搜索与可选 summary；公开价格页需登录"),

    "serper": offering(
        "Serper Google Search API",
        "$50 / 50,000 queries（$1 / 1,000）", 1,
        "$50 top-up / 50k queries", "2,500 queries",
        "Google SERP JSON", True, False,
        "更大充值包最低可到 $0.30/1k queries"),
    "serpapi": offering(
        "SerpApi",
        "Starter $25/月，含 1,000 searches", 25,
        "Starter（1,000 searches/月）", "250 searches/月",
        "多搜索引擎 SERP JSON", True, False,
        "高用量套餐的有效千次单价更低"),
    "google-cse": offering(
        "Google Custom Search JSON API",
        "100 queries/日免费；额外 $5 / 1,000 queries", 5,
        "额外查询（仅存量客户）", "100 queries/日",
        "Google Web/Image Search JSON", True, False,
        "不再向新客户开放；计划于 2027-01-01 停服"),
    "dataforseo": offering(
        "DataForSEO SERP API",
        "Standard queue $0.6/1k；高优先级 $1.2/1k；Live $2/1k", 0.6,
        "Standard queue normal（10 results/SERP）", "$1 注册试用 credit",
        "多搜索引擎 SERP 结构化结果", True, False,
        "最低充值 $50；深度和部分高级参数会增加费用"),
    "brightdata": offering(
        "Bright Data SERP API",
        "PAYG $1.5 / 1,000 successful requests", 1.5,
        "Pay as you go（成功请求）", "5,000 records/月",
        "SERP JSON / HTML / Markdown", True, False,
        "默认失败请求不收费；Scale 额外请求为 $1.3/1k"),
}


def main() -> None:
    cfg = yaml.safe_load((ROOT / "websearch.yaml").read_text(encoding="utf-8"))
    providers = cfg.get("providers") or []
    ids = {item["id"] for item in providers}
    if ids != set(SEED):
        missing = sorted(ids - set(SEED))
        extra = sorted(set(SEED) - ids)
        raise SystemExit(f"websearch.yaml 与种子不一致: missing={missing}, extra={extra}")

    for item in providers:
        pid = item["id"]
        urls = item.get("websearch_urls") or [item.get("websearch_url")]
        record = {
            "provider_name": item["name"],
            "category": item["category"],
            "source": "seed",
            "seed_verified": pid in VERIFIED_SEED,
            "source_url": urls[0],
            "source_urls": urls,
            "has_search": True,
            "offerings": [SEED[pid]],
            "fetched_at": None,
            "status_note": None,
            "last_error": None,
        }
        _atomic_write_json(WEBSEARCH_DIR / f"{pid}.json", record)
        print("  websearch:", pid)


if __name__ == "__main__":
    main()
