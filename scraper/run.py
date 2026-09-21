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
from datetime import datetime, timedelta, timezone
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
# 送进模型的证据预算(字符), 按"去重后的体积"判断: 只有超过才抽样。
# 抽样会丢价格行(coverage_probe.py 实测: 70k 下 seedance 少 2 个型号、
# alibaba-wan 少 3 个价格), 所以配了自动升级兜底: 压缩摘录抽出来的条目
# 比已收录的少时, 会自动用完整正文重抽一次。默认 70k 可省约一半输入。
GENERATION_EVIDENCE_BUDGET = int(
    os.environ.get("GENERATION_EVIDENCE_BUDGET") or 70_000)
GENERATION_FETCH_MAX_CHARS = None  # hash complete cleaned source; bound only the LLM excerpt
GENERATION_RETRY_COOLDOWN_HOURS = 6


def _cooldown_until(hours: float = GENERATION_RETRY_COOLDOWN_HOURS) -> str:
    """抽取失败的退避截止时间(ISO UTC)，避免页面无变化时每小时重试。"""
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


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
    "modes", "pricing", "price_per_image", "price_per_second",
    "comparison_price_type", "price_basis",
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
                        if field in ("price_per_image", "price_per_second",
                                     "comparison_price_type") else
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

# 摘录被抽样压缩时留下的标记。抽样只保留证据窗口, 实测会丢价格行,
# 所以一旦它出现, 结果里"条目变少"就必须先怀疑是压缩造成的。
EXCERPT_WINDOW_MARKER = "… [本来源证据窗口] …"


def _excerpt_was_sampled(text: str) -> bool:
    return EXCERPT_WINDOW_MARKER in text


def _generation_coverage_would_lose(previous: list, candidate: list) -> bool:
    """候选结果是否比已收录事实"少"(条目数变少或有的条目不见了)。"""
    if not previous:
        return False
    if len(candidate) < len(previous):
        return True
    previous_ids = {_generation_offering_identity(item) for item in previous}
    candidate_ids = {_generation_offering_identity(item) for item in candidate}
    return not previous_ids.issubset(candidate_ids)

# 生命周期(停用/下线)证据必须完整送给模型: 它决定产品是否还在售, 截断会导致
# 错误结论。超过这个量就明确拒绝本轮抽取, 而不是静默采样。
# 该阈值与价格证据预算解耦, 因此调小预算不会让更多页面被拒绝。
LIFECYCLE_EVIDENCE_MAX = 180_000


_FACT_BEARING_LINE = re.compile(r"\d")


def _dedupe_repeated_lines(text: str) -> str:
    """丢掉重复样板块(导航/页脚/重复区块), 但保留不同上下文里的相同行。

    只有"上一行 + 本行"整体重复时才丢弃: 同一个价格行出现在不同产品下面
    属于不同事实(例如两行 "$0.04 per image" 分属两个模型), 不能合并。
    重复块不携带新事实, 却会挤满关键词命中位置, 让抽样把预算浪费在样板
    而不是不同的价格行上。
    """
    seen = set()
    kept = []
    previous = ""
    for line in text.split("\n"):
        stripped = line.strip()
        # 含数字的行一律保留: 价格/规格/额度一定带数字, 宁可少省也不能丢事实。
        if stripped and not _FACT_BEARING_LINE.search(stripped):
            key = (previous, stripped)
            if key in seen:
                continue
            seen.add(key)
        kept.append(line)
        previous = stripped
    return "\n".join(kept)


def _generation_source_excerpt(text: str, limit: int) -> str:
    """保留全部生命周期证据, 再按 limit 采样价格/规格证据窗口。

    limit 只约束价格/规格证据部分: 真实官网页面常在 2 万~16 万字符,
    全量发送会让单次输入达到数万 token。先做去重, 仍超预算才抽样。
    """
    text = _dedupe_repeated_lines(text)
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
    if len(lifecycle_text) > LIFECYCLE_EVIDENCE_MAX:
        return LIFECYCLE_EXCERPT_OVERFLOW
    head_budget = max(400, limit // 20)
    tail_budget = max(400, limit // 20)
    general_budget = max(
        0, limit - len(lifecycle_text) - head_budget - tail_budget - 160)
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
        span = max(1, general_budget)
        evidence_text = text[middle - span // 2:middle + span // 2]
    # 整体不再按 limit 截断: 生命周期证据必须完整, 价格部分已各自受限。
    return (text[:head_budget] + f"\n{EXCERPT_WINDOW_MARKER}\n" + evidence_text
            + "\n… [本来源尾部] …\n" + text[-tail_budget:])


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
    fetched: list[tuple[str, str]] = []
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
        fetched.append((url, body))
    # 先做无损去重, 再拿"去重后的体积"判断预算: 否则页脚/导航的重复样板
    # 会把体积顶过预算, 让本来装得下的页面也被抽样, 白白丢证据。
    prepared = [(url, _dedupe_repeated_lines(body))
                for url, body in fetched]
    total_chars = sum(len(body) for _url, body in prepared)
    for url, body in prepared:
        if total_chars <= GENERATION_EVIDENCE_BUDGET:
            # 去重后仍在预算内: 不抽样, 不存在丢证据的风险。
            excerpt = body
        else:
            # 按来源体量分配预算, 而不是按来源数量平分:
            # 否则多来源厂商的短页面也会被误砍, 丢掉价格行。
            share = max(1, int(GENERATION_EVIDENCE_BUDGET
                               * len(body) / total_chars))
            excerpt = _generation_source_excerpt(body, share)
        texts.append(f"===== 官网媒体生成页: {url} =====\n{excerpt}")
    complete_hash = _sha("\0\0".join(fingerprint_parts))
    return urls[0], "\n\n".join(texts), warnings, complete_hash


def _fetch_imagegen_text(cfg: dict) -> tuple[str, str, list[str], str]:
    return _fetch_generation_text(cfg, "imagegen")


def _fetch_videogen_text(cfg: dict) -> tuple[str, str, list[str], str]:
    return _fetch_generation_text(cfg, "videogen")


def _fetch_generation_text_full(cfg: dict, key: str) -> tuple:
    """升级路径: 临时关掉抽样, 重新取完整正文。

    只在"压缩摘录抽出来的条目比已收录的少"时调用, 用来确认变少是真的
    还是抽样造成的。会多花一次抓取, 但这种情况很少见。
    """
    global GENERATION_EVIDENCE_BUDGET
    original = GENERATION_EVIDENCE_BUDGET
    GENERATION_EVIDENCE_BUDGET = 10 ** 9
    try:
        return _fetch_generation_text(cfg, key)
    finally:
        GENERATION_EVIDENCE_BUDGET = original


def _fetch_imagegen_text_full(cfg: dict) -> tuple:
    return _fetch_generation_text_full(cfg, "imagegen")


def _fetch_videogen_text_full(cfg: dict) -> tuple:
    return _fetch_generation_text_full(cfg, "videogen")


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


def _pricing_row_identity(model: dict) -> tuple:
    """Stable row identity for a same-page extraction-schema refresh."""
    return (
        str(model.get("model") or "").strip().casefold(),
        model.get("input_per_1m"),
        model.get("output_per_1m"),
        model.get("cached_input_per_1m"),
        str(model.get("currency") or "").strip().upper(),
    )


def _merge_revision_only_pricing(previous: list[dict], current: list[dict]):
    """Fill newly introduced fields without rewriting facts from unchanged HTML."""
    buckets: dict[tuple, list[dict]] = {}
    for model in current:
        buckets.setdefault(_pricing_row_identity(model), []).append(model)
    merged = []
    for old in previous:
        candidates = buckets.get(_pricing_row_identity(old)) or []
        if not candidates:
            return None
        fresh = candidates.pop(0)
        row = dict(old)
        for key, value in fresh.items():
            if key not in row or row[key] is None or row[key] == "" or row[key] == []:
                row[key] = value
        merged.append(row)
    if len(merged) != len(current) or any(buckets.values()):
        return None
    return merged


def process_provider(cfg: dict) -> None:
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    now = utcnow()
    prev = history.load_provider(pid) or {}
    record = dict(prev)
    extraction_revision = int(cfg.get("pricing_extraction_revision") or 1)
    if extraction_revision < 1:
        raise ValueError("pricing_extraction_revision 必须是正整数")
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

    previous_revision = int(prev.get("pricing_extraction_revision") or 1)
    if (prev.get("price_hash") == page_hash
            and previous_revision == extraction_revision):
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
    revision_only = (prev.get("price_hash") == page_hash
                     and previous_revision != extraction_revision)
    preserve_revision_facts = False
    if revision_only and prev.get("models") and new_models:
        merged = _merge_revision_only_pricing(prev["models"], new_models)
        if merged is None:
            message = ("官网页面未变化，但新版抽取的价格行与已接受事实不一致；"
                       "保留旧数据并等待重试")
            record.update({"status_note": message, "last_error": message})
            history.save_provider(pid, record)
            print(f"[{pid}] 抽取版本刷新不一致，保留旧数据")
            return
        new_models = merged
        preserve_revision_facts = True

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
    if (prev.get("source") == "claude" and prev.get("models")
            and prev.get("price_hash") != page_hash):
        diffs = history.diff_models(prev["models"], new_models)
        if diffs:
            history.append_changes([
                {"ts": now, "provider": pid, "provider_name": name, **d}
                for d in diffs
            ])

    accepted_currency = (prev.get("currency") if preserve_revision_facts
                         else page.currency)
    accepted_promotions = (prev.get("promotions") if preserve_revision_facts
                           else page.promotions)
    accepted_page_has_pricing = (prev.get("page_has_pricing")
                                 if preserve_revision_facts
                                 else page.page_has_pricing)
    record.update({
        "source": "claude",
        "currency": accepted_currency,
        "promotions": accepted_promotions,
        "models": new_models,
        "page_has_pricing": accepted_page_has_pricing,
        "price_hash": page_hash,
        "pricing_extraction_revision": extraction_revision,
        "fetched_at": now,
        "status_note": (None if accepted_page_has_pricing
                        else "页面未见价格表(可能 JS 渲染)"),
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


WEBSEARCH_RETRY_FIELD = "websearch_retry_after"


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
    # 页面没变时: 上次成功过就跳过; 上次失败过则等冷却结束再试。
    # 不能只看 last_error 就无条件重抽 —— 永久性失败(页面确实没有可用信息)
    # 会因此每小时都白烧一次调用。
    if prev.get("websearch_hash") == page_hash:
        if not prev.get("last_error"):
            record["status_note"] = "页面无变化"
            history.save_websearch(pid, record)
            print(f"[{pid}/websearch] 页面无变化, 跳过抽取")
            return
        if prev.get(WEBSEARCH_RETRY_FIELD) and prev[WEBSEARCH_RETRY_FIELD] > now:
            record["status_note"] = "页面无变化且上次处理未成功，冷却期内暂不重试"
            history.save_websearch(pid, record)
            print(f"[{pid}/websearch] 页面无变化，冷却期内暂不重试")
            return
    if not extract.has_api_key():
        record["status_note"] = "等待 OPENAI_API_KEY"
        history.save_websearch(pid, record)
        return

    try:
        page = extract.extract_websearch(name, source_url, text)
    except Exception as exc:
        message = f"官网联网搜索抽取失败: {exc}"
        record.update({
            "websearch_hash": page_hash,
            WEBSEARCH_RETRY_FIELD: _cooldown_until(),
            "status_note": message[:300],
            "last_error": message[:300],
        })
        history.save_websearch(pid, record)
        print(f"[{pid}/websearch] {message}")
        return
    offerings = [o.model_dump() for o in page.offerings]
    if not offerings and prev.get("offerings"):
        message = f"官网页未解析出联网搜索信息，已保留上次 {len(prev['offerings'])} 条"
        record.update({
            "websearch_hash": page_hash,
            "has_search": page.has_search,
            "status_note": message,
            "last_error": message,
            WEBSEARCH_RETRY_FIELD: _cooldown_until(),
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
        # 成功必须清掉上次的错误与冷却, 否则下一小时页面没变也会继续重抽。
        "last_error": None,
    })
    record.pop(WEBSEARCH_RETRY_FIELD, None)
    history.save_websearch(pid, record)
    print(f"[{pid}/websearch] 抽取到 {len(offerings)} 条")


def _process_generation(cfg: dict, key: str) -> bool:
    """处理一个生图/生视频厂商；返回本轮是否调用了抽取模型。"""
    settings = {
        "imagegen": {
            "load": history.load_imagegen,
            "save": history.save_imagegen,
            "fetch": _fetch_imagegen_text,
            "fetch_full": _fetch_imagegen_text_full,
            "extract": extract.extract_imagegen,
            "hash": "imagegen_hash",
            "has": "has_image_generation",
            "label": "生图",
        },
        "videogen": {
            "load": history.load_videogen,
            "save": history.save_videogen,
            "fetch": _fetch_videogen_text,
            "fetch_full": _fetch_videogen_text_full,
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
    retry_after_field = f"{key}_retry_after"
    if prev.get(hash_field) == page_hash and not prev.get(f"{key}_pending_shrink_hash"):
        if not prev.get("last_error"):
            record["status_note"] = "页面无变化"
            settings["save"](pid, record)
            print(f"[{pid}/{key}] 页面无变化, 跳过抽取")
            return False
        if prev.get(retry_after_field) and prev.get(retry_after_field) > now:
            record["status_note"] = "页面无变化且上次处理未成功，冷却期内暂不重试"
            settings["save"](pid, record)
            print(f"[{pid}/{key}] 页面无变化，冷却期内暂不重试")
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
        record.update({
            hash_field: page_hash,
            excerpt_hash_field: excerpt_hash,
            retry_after_field: _cooldown_until(),
            "last_error": message[:300],
            "status_note": message[:300],
        })
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True

    # 摘录被抽样压缩过时, "条目变少"可能只是压缩造成的。先用完整正文重抽一次,
    # 只有完整正文也确认变少, 才继续走"等待下轮同语义确认"那条路。
    if _excerpt_was_sampled(text) and _generation_coverage_would_lose(
            prev.get("offerings") or [],
            [item.model_dump() for item in page.offerings]):
        full_fetched = None
        try:
            full_fetched = settings["fetch_full"](cfg)
        except FetchError as exc:
            print(f"[{pid}/{key}] 取完整正文失败, 沿用压缩摘录结果: {exc}",
                  file=sys.stderr)
        if full_fetched and not _excerpt_was_sampled(full_fetched[1]):
            try:
                upgraded = settings["extract"](
                    name, source_url, full_fetched[1])
            except Exception as exc:
                print(f"[{pid}/{key}] 完整正文重抽失败, 沿用压缩摘录结果: {exc}",
                      file=sys.stderr)
            else:
                page = upgraded
                text = full_fetched[1]
                excerpt_hash = _sha(text)
                print(f"[{pid}/{key}] 压缩摘录疑似丢条目, 已用完整正文重抽")

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
            retry_after_field: _cooldown_until(),
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
            retry_after_field: _cooldown_until(),
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
        record.update({hash_field: page_hash, retry_after_field: _cooldown_until(),
                       "status_note": message, "last_error": message})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    new_identities = [_generation_offering_identity(item) for item in offerings]
    if len(new_identities) != len(set(new_identities)):
        message = f"官网{settings['label']}抽取出现重复条目，保留上次数据并重试"
        record.pop(pending_field, None)
        record.pop(pending_count_field, None)
        record.update({hash_field: page_hash, retry_after_field: _cooldown_until(),
                       "status_note": message, "last_error": message})
        settings["save"](pid, record)
        print(f"[{pid}/{key}] {message}")
        return True
    coverage_loss = _generation_coverage_would_lose(
        previous_offerings, offerings)
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

    extract.reset_usage()
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

        print(extract.usage_summary())

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
