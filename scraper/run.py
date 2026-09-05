"""主流程: 抓取官网页面 -> 内容有变化时用 Claude 抽取 -> 记录价格变动
与新增公告 -> 更新汇率 -> 重新生成静态站点。

设计原则:
- 任何单厂商失败都不中断整体(保留旧数据, 把错误写进状态)
- 没有 OPENAI_API_KEY 时跳过抽取, 仅用已有数据建站
- 页面内容 hash 没变就完全不调 Claude —— 绝大多数小时级运行是零成本的
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml

from . import extract, history
from .fetch import FetchError, fetch, fetch_rendered
from .history import utcnow

PROVIDERS_YAML = Path(__file__).resolve().parent.parent / "providers.yaml"


def load_providers() -> list:
    cfg = yaml.safe_load(PROVIDERS_YAML.read_text(encoding="utf-8"))
    return cfg["providers"]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_language(cfg: dict) -> str:
    """国际厂商固定抓英文官网，国内厂商默认抓中文官网。"""
    configured = str(cfg.get("language") or "").strip()
    if configured:
        return configured
    return "en-US" if cfg.get("region") == "国际" else "zh-CN"


def _fetch_pricing_text(cfg: dict) -> tuple[str, str]:
    """抓取价格页文本。支持单 url / pricing_urls 多页拼接 / JS 渲染。"""
    urls = cfg.get("pricing_urls") or (
        [cfg["pricing_url"]] if cfg.get("pricing_url") else [])
    if not urls:
        raise FetchError("providers.yaml 未配置 pricing_url")
    language = _fetch_language(cfg)
    texts = []
    for u in urls:
        body = (fetch_rendered(u, language=language)
                if cfg.get("render") else fetch(u, language=language))
        texts.append(f"===== 页面: {u} =====\n{body}")
    return urls[0], "\n\n".join(texts)


def _fetch_news_text(cfg: dict) -> str:
    """抓取公告页；对声明为动态页面的来源启用浏览器渲染。"""
    language = _fetch_language(cfg)
    if cfg.get("news_render"):
        return fetch_rendered(
            cfg["news_url"], preserve_links=True, language=language)
    return fetch(cfg["news_url"], language=language)


def _fetch_plan_text(cfg: dict) -> tuple[str, str]:
    """抓取套餐页；可合并官网的价格页与额度说明页。"""
    urls = cfg.get("plan_urls") or (
        [cfg["plan_url"]] if cfg.get("plan_url") else [])
    if not urls:
        raise FetchError("providers.yaml 未配置 plan_url")
    language = _fetch_language(cfg)
    texts = []
    for url in urls:
        body = (fetch_rendered(url, language=language)
                if cfg.get("plan_render") else fetch(url, language=language))
        texts.append(f"===== 官网套餐页: {url} =====\n{body}")
    return urls[0], "\n\n".join(texts)


def _fetch_websearch_text(cfg: dict) -> str:
    """抓取联网搜索能力/定价页；对声明为动态页面的来源启用浏览器渲染。"""
    language = _fetch_language(cfg)
    if cfg.get("websearch_render"):
        return fetch_rendered(cfg["websearch_url"], language=language)
    return fetch(cfg["websearch_url"], language=language)


def _absolute_news_url(source_url: str, candidate) -> str | None:
    """把公告条目的相对链接补全；拒绝非 HTTP(S) 协议。"""
    if not candidate:
        return None
    absolute = urljoin(source_url, str(candidate).strip())
    parsed = urlparse(absolute)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        return absolute
    return None


def _official_plan_url(cfg: dict, candidate) -> str | None:
    """只允许套餐来源指向配置官网的同一主域，拒绝推广链接。"""
    if not candidate:
        return None
    sources = cfg.get("plan_urls") or (
        [cfg["plan_url"]] if cfg.get("plan_url") else [])
    if not sources:
        return None
    absolute = urljoin(sources[0], str(candidate).strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None

    def root_domain(url: str) -> str:
        host = (urlparse(url).hostname or "").lower().strip(".")
        labels = host.split(".")
        return ".".join(labels[-2:]) if len(labels) >= 2 else host

    allowed_roots = {root_domain(url) for url in sources}
    return absolute if root_domain(absolute) in allowed_roots else None


def _news_fingerprint(source_url: str, text: str) -> str:
    """以公告链接集合生成稳定指纹，忽略动态页面的时间和推荐文案噪声。"""
    links = set()
    for title, target in re.findall(r"\[([^\]\n]+)\]\(([^)\n]+)\)", text):
        absolute = _absolute_news_url(source_url, target)
        if not absolute:
            continue
        normalized_title = re.sub(r"\s+", " ", title).strip()
        if normalized_title:
            links.add(f"{normalized_title}\t{absolute}")
    if links:
        return _sha("\n".join(sorted(links)))
    return _sha(re.sub(r"\s+", " ", text).strip())


def process_provider(cfg: dict) -> None:
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = history.load_provider(pid) or {}
    record = dict(prev)
    record["url"] = (cfg.get("pricing_urls") or [cfg.get("pricing_url")])[0]

    try:
        first_url, text = _fetch_pricing_text(cfg)
    except FetchError as exc:
        record["last_error"] = str(exc)[:300]
        record["last_fetch_ts"] = now
        history.save_provider(pid, record)
        print(f"[{pid}] 抓取失败: {exc}")
        return

    record["last_error"] = None
    record["last_fetch_ts"] = now
    page_hash = _sha(text)

    if prev.get("price_hash") == page_hash:
        record["status_note"] = "页面无变化"
        history.save_provider(pid, record)
        print(f"[{pid}] 页面无变化, 跳过抽取")
        return

    if not extract.has_api_key():
        record["status_note"] = "等待 OPENAI_API_KEY"
        history.save_provider(pid, record)
        print(f"[{pid}] 内容有变化, 但未配置 API key, 保留旧数据")
        return

    page = extract.extract_pricing(name, first_url, text)
    new_models = [m.model_dump() for m in page.models]

    # 空结果通常意味着页面结构、人机验证或抽取暂时异常。已有真实价格时，
    # 宁可保留上次数据并标记为陈旧，也不能把整个厂商的模型价格清空。
    # 仍记录本次页面 hash，避免同一份无法解析的页面每小时重复调用大模型。
    if not new_models and prev.get("models"):
        message = f"官网页面未解析出有效价格，已保留上次 {len(prev['models'])} 个模型"
        record.update({
            "page_has_pricing": page.page_has_pricing,
            "price_hash": page_hash,
            "status_note": message,
            "last_error": message,
        })
        history.save_provider(pid, record)
        print(f"[{pid}] 抽取结果为空, 保留上次 {len(prev['models'])} 个模型")
        return

    # 只有旧数据也来自真实抽取时才记变动; 种子数据 -> 首次抽取是初始化
    if prev.get("source") == "claude" and prev.get("models"):
        diffs = history.diff_models(prev["models"], new_models)
        if diffs:
            history.append_changes([
                {"ts": now, "provider": pid, "provider_name": name, **d}
                for d in diffs
            ])

    record.update({
        "source": "claude",
        "currency": page.currency,
        "promotions": page.promotions,
        "models": new_models,
        "page_has_pricing": page.page_has_pricing,
        "price_hash": page_hash,
        "fetched_at": now,
        "status_note": None if page.page_has_pricing else "页面未见价格表(可能 JS 渲染)",
    })
    history.save_provider(pid, record)
    print(f"[{pid}] 抽取到 {len(new_models)} 个模型")


def process_news(cfg: dict) -> None:
    if not cfg.get("news_url"):
        return
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = history.load_news(pid)

    try:
        text = _fetch_news_text(cfg)
    except FetchError as exc:
        print(f"[{pid}/news] 抓取失败: {exc}")
        return

    page_hash = _news_fingerprint(cfg["news_url"], text)
    if prev.get("news_hash") == page_hash:
        return
    if not extract.has_api_key():
        return

    page = extract.extract_news(name, cfg["news_url"], text)
    known = {}
    for old in prev.get("entries", []):
        known[(old.get("title") or "").strip()] = old.get("first_seen", now)
    entries = []
    for e in page.entries:
        d = e.model_dump()
        title = (d.get("title") or "").strip()
        if not title:
            continue
        d["url"] = _absolute_news_url(cfg["news_url"], d.get("url"))
        d["first_seen"] = known.get(title, now)
        entries.append(d)
    if not entries and prev.get("entries"):
        message = f"公告页未解析出有效条目，已保留上次 {len(prev['entries'])} 条"
        record = dict(prev)
        record.update({
            "entries": prev["entries"],
            "news_hash": page_hash,
            "fetched_at": now,
            "status_note": message,
            "last_error": message,
        })
        history.save_news(pid, record)
        print(f"[{pid}/news] {message}")
        return
    history.save_news(pid, {
        "entries": entries[:history.MAX_NEWS_PER_PROVIDER],
        "news_hash": page_hash,
        "fetched_at": now,
        "status_note": None,
        "last_error": None,
    })
    print(f"[{pid}/news] {len(entries)} 条公告")


def process_plans(cfg: dict) -> None:
    if not (cfg.get("plan_url") or cfg.get("plan_urls")):
        return
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = history.load_plans(pid)
    record = dict(prev)

    try:
        first_url, text = _fetch_plan_text(cfg)
    except FetchError as exc:
        record.update({"last_error": str(exc)[:300], "last_fetch_ts": now})
        history.save_plans(pid, record)
        print(f"[{pid}/plans] 抓取失败: {exc}")
        return

    record.update({"last_error": None, "last_fetch_ts": now})
    page_hash = _sha(text)
    if prev.get("plans_hash") == page_hash:
        record["status_note"] = "页面无变化"
        history.save_plans(pid, record)
        print(f"[{pid}/plans] 页面无变化, 跳过抽取")
        return
    if not extract.has_api_key():
        record["status_note"] = "等待 OPENAI_API_KEY"
        history.save_plans(pid, record)
        return

    page = extract.extract_plans(name, first_url, text)
    plans = []
    for plan in page.plans:
        if not plan.quotas:
            continue
        item = plan.model_dump()
        item["source_url"] = (_official_plan_url(cfg, item.get("source_url"))
                              or first_url)
        plans.append(item)
    if not plans and prev.get("plans"):
        message = f"官网页未解析出有效套餐，已保留上次 {len(prev['plans'])} 个"
        record.update({
            "plans_hash": page_hash,
            "page_has_plans": page.page_has_plans,
            "status_note": message,
            "last_error": message,
        })
        history.save_plans(pid, record)
        return

    record.update({
        "source": "official",
        "source_urls": cfg.get("plan_urls") or [cfg.get("plan_url")],
        "plans": plans,
        "page_has_plans": page.page_has_plans,
        "plans_hash": page_hash,
        "fetched_at": now,
        "status_note": None,
    })
    history.save_plans(pid, record)
    print(f"[{pid}/plans] 抽取到 {len(plans)} 个官网套餐")


def process_websearch(cfg: dict) -> None:
    if not cfg.get("websearch_url"):
        return
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = history.load_websearch(pid)
    record = dict(prev)

    try:
        text = _fetch_websearch_text(cfg)
    except FetchError as exc:
        record.update({"last_error": str(exc)[:300], "last_fetch_ts": now})
        history.save_websearch(pid, record)
        print(f"[{pid}/websearch] 抓取失败: {exc}")
        return

    record.update({"last_error": None, "last_fetch_ts": now})
    page_hash = _sha(text)
    if prev.get("websearch_hash") == page_hash:
        record["status_note"] = "页面无变化"
        history.save_websearch(pid, record)
        print(f"[{pid}/websearch] 页面无变化, 跳过抽取")
        return
    if not extract.has_api_key():
        record["status_note"] = "等待 OPENAI_API_KEY"
        history.save_websearch(pid, record)
        return

    page = extract.extract_websearch(name, cfg["websearch_url"], text)
    offerings = [o.model_dump() for o in page.offerings]
    if not offerings and prev.get("offerings"):
        message = f"官网页未解析出联网搜索信息，已保留上次 {len(prev['offerings'])} 条"
        record.update({
            "websearch_hash": page_hash,
            "has_search": page.has_search,
            "status_note": message,
            "last_error": message,
        })
        history.save_websearch(pid, record)
        print(f"[{pid}/websearch] {message}")
        return

    record.update({
        "source": "official",
        "source_url": cfg["websearch_url"],
        "has_search": page.has_search,
        "offerings": offerings,
        "websearch_hash": page_hash,
        "fetched_at": now,
        "status_note": None,
    })
    history.save_websearch(pid, record)
    print(f"[{pid}/websearch] 抽取到 {len(offerings)} 条")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓取大模型官网价格并生成对比站点")
    ap.add_argument("--build-only", action="store_true",
                    help="跳过抓取与抽取, 只用现有数据重建站点")
    ap.add_argument("--only", help="只处理指定 provider id (调试用)")
    args = ap.parse_args(argv)

    providers = load_providers()
    if args.only:
        providers = [p for p in providers if p["id"] == args.only]

    if not args.build_only:
        for cfg in providers:
            try:
                process_provider(cfg)
            except Exception as exc:  # 单厂商失败不拖垮整体
                print(f"[{cfg['id']}] 处理出错: {exc}", file=sys.stderr)
        for cfg in providers:
            try:
                process_news(cfg)
            except Exception as exc:
                print(f"[{cfg['id']}/news] 处理出错: {exc}", file=sys.stderr)
        for cfg in providers:
            try:
                process_plans(cfg)
            except Exception as exc:
                print(f"[{cfg['id']}/plans] 处理出错: {exc}", file=sys.stderr)
        for cfg in providers:
            try:
                process_websearch(cfg)
            except Exception as exc:
                print(f"[{cfg['id']}/websearch] 处理出错: {exc}", file=sys.stderr)

        from .fx import update_fx
        meta = history.load_meta()
        meta["fx"] = update_fx()
        meta["generated_at"] = utcnow()
        history.save_meta(meta)

    from . import build_site
    out = build_site.build(providers)
    print(f"站点已生成: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
