"""生成联网搜索对比的种子数据 data/websearch/*.json。

种子数据是各厂商官网联网搜索能力的手工整理(客观事实), 让「联网搜索」
板块在首次成功抓取解析官网之前不至于空白; source 标为 "seed", 首次抓取
成功后自动替换(站点上显示「种子数据 · 待校准」徽标)。

只收录客观、可核验的事实; 官网未明确说明的维度填 null(页面显示 —)。
用法: python3 scripts/make_websearch.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scraper.history import WEBSEARCH_DIR, _atomic_write_json  # noqa: E402

# name / cites_sources / default_on / pricing / note 均为客观口径;
# 不确定的一律 None, 首次官网抓取后自动校准。
SEED = {
    "anthropic": {
        "has_search": True,
        "offerings": [{
            "name": "Web search tool",
            "pricing": "按搜索次数额外计费(独立于 token 价)",
            "cites_sources": True,
            "default_on": False,
            "note": "返回带引用的来源; 支持多轮搜索与动态过滤(web_search_20260209+)",
        }],
    },
    "openai": {
        "has_search": True,
        "offerings": [{
            "name": "Search API / web_search 工具",
            "pricing": "gpt-5-search-api 按 token 计价($1.25/$10 每百万)",
            "cites_sources": True,
            "default_on": False,
            "note": "Search API 独立计费; ChatGPT 内置搜索另计",
        }],
    },
    "google": {
        "has_search": True,
        "offerings": [{
            "name": "Grounding with Google Search",
            "pricing": "按 grounded 查询次数额外计费",
            "cites_sources": True,
            "default_on": False,
            "note": "自动生成搜索查询并返回内联引用(annotations)",
        }],
    },
    "xai": {
        "has_search": True,
        "offerings": [{
            "name": "Grok 联网搜索 / API web search",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
    "mistral": {
        "has_search": True,
        "offerings": [{
            "name": "Web search (Le Chat / API)",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
    "deepseek": {
        "has_search": True,
        "offerings": [{
            "name": "联网搜索 (API)",
            "pricing": None,
            "cites_sources": None,
            "default_on": False,
            "note": "待官网首次抓取校准",
        }],
    },
    "qwen": {
        "has_search": True,
        "offerings": [{
            "name": "百炼 联网搜索",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
    "doubao": {
        "has_search": True,
        "offerings": [{
            "name": "火山方舟 联网搜索",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
    "zhipu": {
        "has_search": True,
        "offerings": [{
            "name": "GLM 联网搜索 (web_search)",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
    "moonshot": {
        "has_search": True,
        "offerings": [{
            "name": "Kimi 联网搜索",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
    "minimax": {
        "has_search": True,
        "offerings": [{
            "name": "MiniMax 联网搜索",
            "pricing": None,
            "cites_sources": None,
            "default_on": None,
            "note": "待官网首次抓取校准",
        }],
    },
}

# 官网入口(与 providers.yaml 的 websearch_url 保持一致, 用于站点「官网 ↗」链接)
SOURCE_URLS = {
    "anthropic": "https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool",
    "openai": "https://developers.openai.com/api/docs/guides/web-search",
    "google": "https://ai.google.dev/gemini-api/docs/google-search",
    "xai": "https://docs.x.ai/docs/tools",
    "mistral": "https://docs.mistral.ai/capabilities/web-search/",
    "deepseek": "https://api-docs.deepseek.com/zh-cn/guides/web_search",
    "qwen": "https://help.aliyun.com/zh/model-studio/web-search",
    "doubao": "https://www.volcengine.com/docs/82379/1500601",
    "zhipu": "https://docs.bigmodel.cn/cn/guide/tools/web-search",
    "moonshot": "https://platform.kimi.com/docs/guide/web-search",
    "minimax": "https://platform.minimaxi.com/docs/guides/chat",
}


def main() -> None:
    for pid, facts in SEED.items():
        record = {
            "source": "seed",
            "source_url": SOURCE_URLS.get(pid),
            "has_search": facts["has_search"],
            "offerings": facts["offerings"],
            "fetched_at": None,
            "status_note": None,
            "last_error": None,
        }
        _atomic_write_json(WEBSEARCH_DIR / f"{pid}.json", record)
        print("  websearch:", pid)


if __name__ == "__main__":
    main()
