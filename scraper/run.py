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
import sys
from pathlib import Path

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


def _fetch_pricing_text(cfg: dict) -> tuple[str, str]:
    """抓取价格页文本。支持单 url / pricing_urls 多页拼接 / JS 渲染。"""
    urls = cfg.get("pricing_urls") or (
        [cfg["pricing_url"]] if cfg.get("pricing_url") else [])
    if not urls:
        raise FetchError("providers.yaml 未配置 pricing_url")
    texts = []
    for u in urls:
        body = fetch_rendered(u) if cfg.get("render") else fetch(u)
        texts.append(f"===== 页面: {u} =====\n{body}")
    return urls[0], "\n\n".join(texts)


def _fetch_news_text(cfg: dict) -> str:
    """抓取公告页；对声明为动态页面的来源启用浏览器渲染。"""
    if cfg.get("news_render"):
        return fetch_rendered(cfg["news_url"])
    return fetch(cfg["news_url"])


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

    page_hash = _sha(text)
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
        d["first_seen"] = known.get(title, now)
        entries.append(d)
    history.save_news(pid, {
        "entries": entries[:history.MAX_NEWS_PER_PROVIDER],
        "news_hash": page_hash,
        "fetched_at": now,
    })
    print(f"[{pid}/news] {len(entries)} 条公告")


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
