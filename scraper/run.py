"""主流程: 抓取官网页面 -> 内容有变化时用兼容 OpenAI 的模型抽取 -> 记录价格变动
与新增公告 -> 更新汇率 -> 重新生成静态站点。

设计原则:
- 任何单厂商失败都不中断整体(保留旧数据, 把错误写进状态)
- 没有 OPENAI_API_KEY 时跳过抽取, 仅用已有数据建站
- 页面内容 hash 没变就完全不调用抽取模型 —— 绝大多数小时级运行是零成本的
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml

from . import extract, history
from .fetch import FetchError, fetch, fetch_rendered
from .history import utcnow

PROVIDERS_YAML = Path(__file__).resolve().parent.parent / "providers.yaml"
WEBSEARCH_YAML = Path(__file__).resolve().parent.parent / "websearch.yaml"
IMAGEGEN_YAML = Path(__file__).resolve().parent.parent / "imagegen.yaml"
VIDEOGEN_YAML = Path(__file__).resolve().parent.parent / "videogen.yaml"
GENERATION_TEXT_BUDGET = 220_000
GENERATION_FETCH_MAX_CHARS = None  # hash complete cleaned source; bound only the LLM excerpt


def load_providers() -> list:
    cfg = yaml.safe_load(PROVIDERS_YAML.read_text(encoding="utf-8"))
    return cfg["providers"]


def load_websearch_providers() -> list:
    """加载独立的联网搜索目录，避免搜索 API 混入模型价格厂商。"""
    cfg = yaml.safe_load(WEBSEARCH_YAML.read_text(encoding="utf-8"))
    providers = cfg.get("providers") or []
    ids = [item.get("id") for item in providers]
    categories = {"model", "ai-search", "serp"}
    invalid = [item.get("id") for item in providers
               if item.get("category") not in categories
               or not (item.get("websearch_url") or item.get("websearch_urls"))]
    if (not providers or any(not pid for pid in ids)
            or len(ids) != len(set(ids)) or invalid):
        raise ValueError(
            "websearch.yaml 必须包含唯一 id、有效 category 和官网 URL")
    return providers


def _generation_urls(cfg: dict, key: str) -> list[str]:
    urls = list(cfg.get(f"{key}_urls") or [])
    if cfg.get(f"{key}_url"):
        urls.insert(0, cfg[f"{key}_url"])
    for example in cfg.get("examples") or []:
        urls.extend(example[field] for field in ("url", "media_url", "poster_url")
                    if example.get(field))
    return urls


def _official_generation_url_allowed(cfg: dict, url: str) -> bool:
    domains = {str(domain).lower() for domain in (cfg.get("official_domains") or [])}
    prefixes = tuple(str(prefix) for prefix in (cfg.get("official_url_prefixes") or []))
    parsed = urlparse(str(url))
    host = (parsed.hostname or "").lower()
    allowed = parsed.scheme == "https" and host in domains
    if allowed and host in {"github.com", "raw.githubusercontent.com"}:
        allowed = bool(prefixes) and str(url).startswith(prefixes)
    return allowed


def _validate_official_generation_urls(cfg: dict, key: str) -> list[str]:
    return [url for url in _generation_urls(cfg, key)
            if not _official_generation_url_allowed(cfg, url)]


def _load_generation_providers(path: Path, key: str) -> list:
    """加载生图/生视频独立目录并验证唯一 ID 与官网来源。"""
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    providers = cfg.get("providers") or []
    ids = [item.get("id") for item in providers]
    invalid = [item.get("id") for item in providers
               if not (item.get(f"{key}_url") or item.get(f"{key}_urls"))]
    unofficial = {
        item.get("id"): _validate_official_generation_urls(item, key)
        for item in providers if _validate_official_generation_urls(item, key)
    }
    invalid_provenance = [
        item.get("id") for item in providers
        for example in (item.get("examples") or [])
        if example.get("provenance") not in (None, "vendor-published", "official-community")
    ]
    if (not providers or any(not pid for pid in ids)
            or len(ids) != len(set(ids)) or invalid or unofficial
            or invalid_provenance):
        raise ValueError(
            f"{path.name} 必须包含唯一 id、官网 URL 与显式第一方域名；"
            f"不合规 URL={unofficial}，不合规样例来源={invalid_provenance}")
    return providers


def load_imagegen_providers() -> list:
    return _load_generation_providers(IMAGEGEN_YAML, "imagegen")


def load_videogen_providers() -> list:
    return _load_generation_providers(VIDEOGEN_YAML, "videogen")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _generation_offering_identity(offering: dict) -> tuple:
    """Stable coverage identity; preserves punctuation and uses an explicit tier key."""
    normalize = lambda value: re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return (
        normalize(offering.get("name")),
        normalize(offering.get("variant_key") or offering.get("name")),
        offering.get("api_available"),
        str(offering.get("currency") or "").upper(),
        offering.get("comparison_group"),
        offering.get("region"),
        normalize(offering.get("resolution")),
        offering.get("native_audio"),
    )


def _generation_service_regions(offerings: list[dict]) -> list[str]:
    regions = set()
    for offering in offerings:
        region = offering.get("region")
        if region in ("intl", "domestic"):
            regions.add(region)
            continue
        currency = str(offering.get("currency") or "").upper()
        if currency == "CNY":
            regions.add("domestic")
        elif currency:
            regions.add("intl")
        else:
            regions.update(("intl", "domestic"))
    return sorted(regions)


GENERATION_PRESERVED_FIELDS = (
    "modes", "pricing", "price_per_image", "price_per_second", "price_basis",
    "resolution", "duration", "frame_rate", "aspect_ratios", "output_formats",
    "free_quota", "note",
)


def _generation_facts_shrunk(previous: list[dict], candidate: list[dict]) -> bool:
    """Flag same-product populated facts that disappear or list capabilities that shrink."""
    old_by_identity = {_generation_offering_identity(item): item for item in previous}
    for item in candidate:
        old = old_by_identity.get(_generation_offering_identity(item))
        if old is None:
            continue
        for field in GENERATION_PRESERVED_FIELDS:
            old_value = old.get(field)
            new_value = item.get(field)
            if isinstance(old_value, list):
                if old_value and (not isinstance(new_value, list)
                                  or not set(old_value).issubset(set(new_value))):
                    return True
            elif old_value is not None and old_value != "" and new_value in (None, ""):
                return True
    return False


def _generation_lifecycle_changed(previous: list[dict], candidate: list[dict]) -> bool:
    """Treat same-identity lifecycle/status-date changes as two-pass facts."""
    old_by_identity = {_generation_offering_identity(item): item for item in previous}
    for item in candidate:
        old = old_by_identity.get(_generation_offering_identity(item))
        if old is None:
            continue
        old_status = old.get("lifecycle_status") or "unknown"
        new_status = item.get("lifecycle_status") or "unknown"
        if old_status != new_status or old.get("sunset_at") != item.get("sunset_at"):
            return True
    return False


def _generation_candidate_signature(offerings: list[dict], has_value: bool,
                                    product_status: str | None = None,
                                    product_status_date: str | None = None,
                                    product_status_note: str | None = None) -> str:
    """Order/prose-insensitive confirmation key for a potentially lossy extraction."""
    semantic = []
    for offering in offerings:
        semantic.append({
            "identity": _generation_offering_identity(offering),
            "region": offering.get("region"),
            "price_per_image": offering.get("price_per_image"),
            "price_per_second": offering.get("price_per_second"),
            "lifecycle_status": offering.get("lifecycle_status"),
            "sunset_at": offering.get("sunset_at"),
            "preserved_facts": {
                field: (sorted(offering.get(field) or [])
                        if field == "modes" else
                        offering.get(field)
                        if field in ("price_per_image", "price_per_second") else
                        bool(str(offering.get(field) or "").strip()))
                for field in GENERATION_PRESERVED_FIELDS if field != "note"},
        })
    semantic.sort(key=lambda item: repr(item["identity"]))
    encoded = json.dumps(
        {"has": has_value, "product_status": product_status,
         "product_status_date": product_status_date, "offerings": semantic},
        ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False)
    return _sha(encoded)


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


def _fetch_websearch_text(cfg: dict) -> tuple[str, str]:
    """抓取并合并联网搜索能力/定价页；支持一个厂商多个官网来源。"""
    urls = cfg.get("websearch_urls") or (
        [cfg["websearch_url"]] if cfg.get("websearch_url") else [])
    if not urls:
        raise FetchError("websearch.yaml 未配置 websearch_url")
    language = _fetch_language(cfg)
    texts = []
    for url in urls:
        body = (fetch_rendered(url, language=language)
                if cfg.get("websearch_render") else fetch(url, language=language))
        texts.append(f"===== 官网联网搜索页: {url} =====\n{body}")
    return urls[0], "\n\n".join(texts)


LIFECYCLE_EXCERPT_OVERFLOW = "[生命周期证据过多，拒绝不完整抽取]"


def _generation_source_excerpt(text: str, limit: int) -> str:
    """Keep all lifecycle snippets when bounded, then sample price/spec evidence."""
    if len(text) <= limit:
        return text
    lifecycle_pattern = re.compile(
        r"(?i)(deprecated|deprecation|shutdown|sunset|\bEOL\b|end[ -]of[ -]life|"
        r"retir(?:e|ed|ement)|停用|下线|弃用|终止|停售|停止服务)")
    general_pattern = re.compile(
        r"(?i)(pricing|price|cost|credits?|per\s+(?:image|second)|resolution|fps|"
        r"free\s+tier|quota|\$|¥|￥|每(?:张|秒)|元/(?:张|秒)|分辨率|帧率)")
    lifecycle_intervals = []
    for match in lifecycle_pattern.finditer(text):
        left = max(0, match.start() - 40)
        right = min(len(text), match.start() + 100)
        if lifecycle_intervals and left <= lifecycle_intervals[-1][1]:
            lifecycle_intervals[-1][1] = max(lifecycle_intervals[-1][1], right)
        else:
            lifecycle_intervals.append([left, right])
    lifecycle_snippets = [
        re.sub(r"\s+", " ", text[left:right]).strip()
        for left, right in lifecycle_intervals]
    lifecycle_text = "\n".join(snippet for snippet in lifecycle_snippets if snippet)
    head_budget = limit // 20
    tail_budget = limit // 20
    evidence_budget = limit - head_budget - tail_budget - 160
    if len(lifecycle_text) > int(evidence_budget * 0.92):
        return LIFECYCLE_EXCERPT_OVERFLOW
    general_budget = evidence_budget - len(lifecycle_text)
    positions = []
    for match in general_pattern.finditer(text):
        position = match.start()
        if not positions or position - positions[-1] >= 900:
            positions.append(position)
    window = 900
    capacity = max(1, general_budget // window)
    if len(positions) > capacity:
        positions = ([positions[round(i * (len(positions) - 1) / (capacity - 1))]
                      for i in range(capacity)] if capacity > 1
                     else [positions[len(positions) // 2]])
    general = []
    for position in positions:
        left = max(0, position - window // 2)
        general.append(text[left:left + window])
    general_text = "\n… [价格/规格证据] …\n".join(general)[:general_budget]
    evidence_text = lifecycle_text
    if lifecycle_text and general_text:
        evidence_text += "\n… [其它官网事实] …\n"
    evidence_text += general_text
    if not evidence_text:
        middle = len(text) // 2
        evidence_text = text[middle - evidence_budget // 2:middle + evidence_budget // 2]
    return (text[:head_budget] + "\n… [本来源证据窗口] …\n" + evidence_text
            + "\n… [本来源尾部] …\n" + text[-tail_budget:])[:limit]


def _fetch_generation_text(cfg: dict, key: str) -> tuple[str, str, list[str]]:
    """抓取并合并媒体生成的价格、规格和官方样例页。"""
    urls = cfg.get(f"{key}_urls") or (
        [cfg[f"{key}_url"]] if cfg.get(f"{key}_url") else [])
    if not urls:
        raise FetchError(f"{key}.yaml 未配置官网 URL")
    language = _fetch_language(cfg)
    texts = []
    warnings = []
    fingerprint_parts = []
    per_source_budget = max(1, GENERATION_TEXT_BUDGET // len(urls))
    for index, url in enumerate(urls):
        try:
            final_url_validator = lambda final_url: _official_generation_url_allowed(
                cfg, final_url)
            body = (fetch_rendered(
                        url, preserve_links=True, language=language,
                        max_chars=GENERATION_FETCH_MAX_CHARS,
                        final_url_validator=final_url_validator)
                    if cfg.get(f"{key}_render")
                    else fetch(url, language=language,
                               max_chars=GENERATION_FETCH_MAX_CHARS,
                               final_url_validator=final_url_validator))
        except FetchError as exc:
            if index == 0:
                raise FetchError(f"{url}: {exc}") from exc
            warnings.append(f"{url}: {exc}")
            continue
        fingerprint_parts.append(f"{url}\0{body}")
        excerpt = _generation_source_excerpt(body, per_source_budget)
        texts.append(f"===== 官网媒体生成页: {url} =====\n{excerpt}")
    complete_hash = _sha("\0\0".join(fingerprint_parts))
    return urls[0], "\n\n".join(texts), warnings, complete_hash


def _fetch_imagegen_text(cfg: dict) -> tuple[str, str, list[str], str]:
    return _fetch_generation_text(cfg, "imagegen")


def _fetch_videogen_text(cfg: dict) -> tuple[str, str, list[str], str]:
    return _fetch_generation_text(cfg, "videogen")


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

    record["last_fetch_ts"] = now
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
    if not (cfg.get("websearch_url") or cfg.get("websearch_urls")):
        return
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = history.load_websearch(pid)
    record = dict(prev)

    try:
        source_url, text = _fetch_websearch_text(cfg)
    except FetchError as exc:
        record.update({"last_error": str(exc)[:300], "last_fetch_ts": now})
        history.save_websearch(pid, record)
        print(f"[{pid}/websearch] 抓取失败: {exc}")
        return

    record["last_fetch_ts"] = now
    page_hash = _sha(text)
    if (prev.get("websearch_hash") == page_hash
            and not prev.get("last_error")):
        record["status_note"] = "页面无变化"
        history.save_websearch(pid, record)
        print(f"[{pid}/websearch] 页面无变化, 跳过抽取")
        return
    if not extract.has_api_key():
        record["status_note"] = "等待 OPENAI_API_KEY"
        history.save_websearch(pid, record)
        return

    page = extract.extract_websearch(name, source_url, text)
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
        "source_url": source_url,
        "source_urls": cfg.get("websearch_urls") or [source_url],
        "has_search": page.has_search,
        "offerings": offerings,
        "websearch_hash": page_hash,
        "fetched_at": now,
        "status_note": None,
    })
    history.save_websearch(pid, record)
    print(f"[{pid}/websearch] 抽取到 {len(offerings)} 条")


def _process_generation(cfg: dict, key: str) -> bool:
    """处理一个生图/生视频厂商；返回本轮是否调用了抽取模型。"""
    settings = {
        "imagegen": {
            "load": history.load_imagegen,
            "save": history.save_imagegen,
            "fetch": _fetch_imagegen_text,
            "extract": extract.extract_imagegen,
            "hash": "imagegen_hash",
            "has": "has_image_generation",
            "label": "生图",
        },
        "videogen": {
            "load": history.load_videogen,
            "save": history.save_videogen,
            "fetch": _fetch_videogen_text,
            "extract": extract.extract_videogen,
            "hash": "videogen_hash",
            "has": "has_video_generation",
            "label": "生视频",
        },
    }[key]
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = settings["load"](pid)
    record = dict(prev)
    configured_sources = cfg.get(f"{key}_urls") or (
        [cfg[f"{key}_url"]] if cfg.get(f"{key}_url") else [])
    record.update({
        "provider_name": name,
        "category": cfg.get("category"),
        "official_examples": cfg.get("examples") or [],
        "configured_source_urls": configured_sources,
    })

    try:
        fetched = settings["fetch"](cfg)
        source_url, text = fetched[:2]
        source_warnings = fetched[2] if len(fetched) > 2 else []
        complete_source_hash = fetched[3] if len(fetched) > 3 else _sha(text)
    except FetchError as exc:
        record.update({"last_error": str(exc)[:300], "last_fetch_ts": now})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] 抓取失败: {exc}")
        return False

    record["last_fetch_ts"] = now
    record["source_warnings"] = source_warnings
    page_hash = complete_source_hash
    hash_field = settings["hash"]
    excerpt_hash_field = f"{key}_excerpt_hash"
    excerpt_hash = _sha(text)
    prior_facts = bool(prev.get("offerings")) or settings["has"] in prev
    if (prior_facts and prev.get(hash_field)
            and prev.get(hash_field) != page_hash
            and prev.get(excerpt_hash_field) == excerpt_hash):
        message = (f"官网{settings['label']}完整页面已变化但安全摘录无变化；"
                   "保留旧事实且不接受本次网页哈希")
        record.update({"status_note": message, "last_error": message})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    if LIFECYCLE_EXCERPT_OVERFLOW in text:
        message = (f"官网{settings['label']}生命周期证据超过安全抽取预算；"
                   "保留旧事实且不接受本次网页哈希")
        record.update({"status_note": message, "last_error": message})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    if prev.get(hash_field) == page_hash and not prev.get("last_error"):
        record["status_note"] = "页面无变化"
        settings["save"](pid, record)
        print(f"[{pid}/{key}] 页面无变化, 跳过抽取")
        return False
    if not extract.has_api_key():
        record["status_note"] = "等待 OPENAI_API_KEY"
        settings["save"](pid, record)
        return False

    try:
        page = settings["extract"](name, source_url, text)
    except Exception as exc:
        message = f"官网{settings['label']}抽取失败: {exc}"
        record.pop(f"{key}_pending_shrink_hash", None)
        record.pop(f"{key}_pending_shrink_count", None)
        record.update({"last_error": message[:300], "status_note": message[:300]})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    offerings = [item.model_dump() for item in page.offerings]
    has_value = getattr(page, settings["has"])
    page_healthy = getattr(page, "page_has_relevant_content", True)
    product_status = getattr(
        page, "product_status", "active" if has_value else "discontinued")
    product_status_date = getattr(page, "product_status_date", None)
    product_status_note = getattr(page, "product_status_note", None)
    status_contradiction = (
        (has_value is False and product_status != "discontinued")
        or (has_value is True and product_status == "discontinued"))
    if (not page_healthy or status_contradiction
            or (has_value is False and offerings)):
        reason = ("页面是空白/报错/登录/验证壳"
                  if not page_healthy else "抽取结果的产品状态与条目互相矛盾")
        message = f"官网{settings['label']}页面无效: {reason}；保留上次数据"
        record.pop(f"{key}_pending_shrink_hash", None)
        record.pop(f"{key}_pending_shrink_count", None)
        record.update({
            hash_field: page_hash,
            "status_note": message,
            "last_error": message,
        })
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    if not offerings and has_value is not False:
        kept = len(prev.get("offerings") or [])
        message = (f"官网页声明仍有{settings['label']}产品但未解析出条目，"
                   f"已保留上次 {kept} 条")
        record.pop(f"{key}_pending_shrink_hash", None)
        record.pop(f"{key}_pending_shrink_count", None)
        record.update({
            hash_field: page_hash,
            "status_note": message,
            "last_error": message,
        })
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True

    pending_field = f"{key}_pending_shrink_hash"
    pending_count_field = f"{key}_pending_shrink_count"
    previous_offerings = prev.get("offerings") or []
    prior_meaningful = bool(previous_offerings) or any(
        field in prev for field in (settings["has"], "product_status",
                                    "product_status_date", "fetched_at",
                                    "seed_verified"))
    if source_warnings and prior_meaningful:
        message = (f"{len(source_warnings)} 个次要官网来源抓取失败；保留上次完整事实，"
                   "来源恢复后再更新")
        record.pop(pending_field, None)
        record.pop(pending_count_field, None)
        record.update({hash_field: page_hash, "status_note": message,
                       "last_error": message})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    new_identities = [_generation_offering_identity(item) for item in offerings]
    if len(new_identities) != len(set(new_identities)):
        message = f"官网{settings['label']}抽取出现重复条目，保留上次数据并重试"
        record.pop(pending_field, None)
        record.pop(pending_count_field, None)
        record.update({hash_field: page_hash, "status_note": message,
                       "last_error": message})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    previous_identities = {
        _generation_offering_identity(item) for item in previous_offerings}
    candidate_identities = set(new_identities)
    coverage_loss = bool(previous_offerings) and (
        len(offerings) < len(previous_offerings)
        or not previous_identities.issubset(candidate_identities))
    lifecycle_change = _generation_lifecycle_changed(previous_offerings, offerings)
    facts_shrink = _generation_facts_shrunk(previous_offerings, offerings)
    product_values = {
        settings["has"]: has_value,
        "product_status": product_status,
        "product_status_date": product_status_date,
    }
    product_change = prior_meaningful and any(
        field in prev and prev.get(field) != value
        for field, value in product_values.items())
    product_note_loss = (bool(str(prev.get("product_status_note") or "").strip())
                         and not bool(str(product_status_note or "").strip()))
    previous_service_regions = (list(prev.get("service_regions") or [])
                                or _generation_service_regions(previous_offerings))
    candidate_service_regions = _generation_service_regions(offerings)
    service_region_change = bool(previous_service_regions and candidate_service_regions
                                 and previous_service_regions != candidate_service_regions)
    suspicious_change = (coverage_loss or lifecycle_change or facts_shrink
                         or product_change or product_note_loss or service_region_change)
    if suspicious_change:
        candidate_hash = _generation_candidate_signature(
            offerings, has_value, product_status, product_status_date,
            product_status_note)
        if prev.get(pending_field) != candidate_hash:
            change_label = (
                f"覆盖项由 {len(previous_offerings)} 变为 {len(offerings)}"
                if coverage_loss else
                "型号生命周期或停用日期发生变化" if lifecycle_change else
                "已记录能力/额度/规格事实变少" if facts_shrink else
                "产品状态、可用性或停用日期发生变化" if product_change else
                "产品停用/迁移说明变为空" if product_note_loss else
                "服务/价格区域发生变化")
            message = (f"官网{settings['label']}抽取{change_label}，"
                       "等待下轮同语义结果确认")
            record.update({
                hash_field: page_hash,
                pending_field: candidate_hash,
                pending_count_field: len(offerings),
                "status_note": message,
                "last_error": message,
            })
            settings["save"](pid, record)
            print(f"[{pid}/{key}] {message}")
            return True
    record.pop(pending_field, None)
    record.pop(pending_count_field, None)

    service_regions = (_generation_service_regions(offerings)
                       or list(prev.get("service_regions") or [])
                       or _generation_service_regions(previous_offerings))
    record.update({
        "source": "official",
        "source_url": configured_sources[0] if configured_sources else source_url,
        "source_urls": configured_sources or [source_url],
        "configured_source_urls": configured_sources,
        "service_regions": service_regions,
        "page_has_relevant_content": page_healthy,
        "product_status": product_status,
        "product_status_date": product_status_date,
        "product_status_note": product_status_note,
        settings["has"]: has_value,
        "offerings": offerings,
        hash_field: page_hash,
        excerpt_hash_field: excerpt_hash,
        "fetched_at": now,
        "status_note": (f"{len(source_warnings)} 个次要来源抓取失败" if source_warnings else None),
        "last_error": None,
    })
    for seed_field in ("seed_verified", "seed_cutoff", "verified_at",
                       "verification_hash", "seeded_at"):
        record.pop(seed_field, None)
    settings["save"](pid, record)
    print(f"[{pid}/{key}] 抽取到 {len(offerings)} 条")
    return True


def process_imagegen(cfg: dict) -> bool:
    return _process_generation(cfg, "imagegen")


def process_videogen(cfg: dict) -> bool:
    return _process_generation(cfg, "videogen")


def _media_extract_budget() -> int:
    """每种媒体每轮的模型调用上限；0 可临时停用自动校准。"""
    try:
        return max(0, int(os.environ.get("MEDIA_EXTRACT_BUDGET", "5")))
    except ValueError:
        return 5


def _rotate_generation_catalog(catalog: list, key: str,
                               hour_number: int | None = None) -> list:
    """按小时轮换起点，避免前几家持续失败时饿死目录后部厂商。"""
    if not catalog:
        return []
    if hour_number is None:
        hour_number = int(datetime.now(timezone.utc).timestamp() // 3600)
    salt = 0 if key == "imagegen" else max(1, len(catalog) // 2)
    offset = (hour_number + salt) % len(catalog)
    return [*catalog[offset:], *catalog[:offset]]


def _single_writer(func):
    """Serialize every CLI scrape/build entry path, including manual runs."""
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        if os.environ.get("MODEL_PRICE_LOCK_HELD") == "1":
            return func(*args, **kwargs)
        history.DATA.mkdir(parents=True, exist_ok=True)
        lock_path = history.DATA / ".scraper.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(f"已有抓取/构建任务持有锁，跳过本轮: {lock_path}")
                return 75
            try:
                return func(*args, **kwargs)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return wrapped


@_single_writer
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="抓取大模型官网价格并生成对比站点")
    ap.add_argument("--build-only", action="store_true",
                    help="跳过抓取与抽取, 只用现有数据重建站点")
    ap.add_argument("--only", help="只处理指定 provider id (调试用)")
    args = ap.parse_args(argv)

    providers = load_providers()
    websearch_providers = load_websearch_providers()
    imagegen_providers = load_imagegen_providers()
    videogen_providers = load_videogen_providers()
    if args.only:
        providers = [p for p in providers if p["id"] == args.only]
        websearch_providers = [
            p for p in websearch_providers if p["id"] == args.only]
        imagegen_providers = [
            p for p in imagegen_providers if p["id"] == args.only]
        videogen_providers = [
            p for p in videogen_providers if p["id"] == args.only]

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
        for cfg in websearch_providers:
            try:
                process_websearch(cfg)
            except Exception as exc:
                print(f"[{cfg['id']}/websearch] 处理出错: {exc}", file=sys.stderr)

        media_budget = _media_extract_budget()
        for key, catalog, processor in (
                ("imagegen", imagegen_providers, process_imagegen),
                ("videogen", videogen_providers, process_videogen)):
            extracted = 0
            ordered_catalog = _rotate_generation_catalog(catalog, key)
            for cfg in ordered_catalog:
                if extracted >= media_budget:
                    print(f"[{key}] 本轮抽取预算 {media_budget} 已用完，其余下轮继续")
                    break
                try:
                    extracted += int(processor(cfg))
                except Exception as exc:
                    print(f"[{cfg['id']}/{key}] 处理出错: {exc}", file=sys.stderr)

        from .fx import update_fx
        meta = history.load_meta()
        meta["fx"] = update_fx()
        meta["generated_at"] = utcnow()
        history.save_meta(meta)

    from . import build_site
    out = build_site.build(
        providers, websearch_providers, imagegen_providers, videogen_providers)
    print(f"站点已生成: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
