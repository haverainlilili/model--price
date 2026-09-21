"""证据覆盖率探针: 改 GENERATION_EVIDENCE_BUDGET 之前先跑它。

做什么: 抓取各家官网媒体页(缓存到本地), 在不同预算下生成摘录, 检查
"当前已收录的型号名与价格是否还在摘录里", 用来确认压缩没有丢事实。

为什么要它: 去重是无损的, 但**抽样会丢价格行**。历史实测中 70k 预算会让
seedance 少 2 个型号、alibaba-wan 少 3 个价格; 220k(默认)与整页发送的
覆盖率完全一致。任何调小预算的改动都应该先看这张表。

用法:
    python scripts/coverage_probe.py              # 用缓存
    REBUILD_CACHE=1 python scripts/coverage_probe.py   # 重新抓取
    MP_BUDGETS=70000,120000,220000 python scripts/coverage_probe.py
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scraper import run  # noqa: E402
from scraper.fetch import fetch, fetch_rendered  # noqa: E402

DATA = REPO / "data"
CACHE = Path(os.environ.get("MP_BODIES_CACHE") or "/tmp/mp_bodies.json")
BUDGETS = tuple(
    int(item) for item in
    (os.environ.get("MP_BUDGETS") or "70000,120000,220000").split(","))


def catalog():
    return (("imagegen", run.load_imagegen_providers()),
            ("videogen", run.load_videogen_providers()))


def fetch_bodies(cfg, key):
    urls = cfg.get(f"{key}_urls") or (
        [cfg[f"{key}_url"]] if cfg.get(f"{key}_url") else [])
    bodies = []
    for url in urls:
        try:
            validator = lambda u: run._official_generation_url_allowed(cfg, u)  # noqa: E731
            if cfg.get(f"{key}_render"):
                body = fetch_rendered(url, preserve_links=True,
                                      language=run._fetch_language(cfg),
                                      max_chars=run.GENERATION_FETCH_MAX_CHARS,
                                      final_url_validator=validator)
            else:
                body = fetch(url, language=run._fetch_language(cfg),
                             max_chars=run.GENERATION_FETCH_MAX_CHARS,
                             final_url_validator=validator)
            bodies.append(body)
        except Exception as exc:  # 次要来源失败不应中断探针
            print(f"    (来源失败 {url[:50]}: {str(exc)[:40]})", flush=True)
    return bodies


def build_cache():
    cache = {}
    for key, providers in catalog():
        for cfg in providers:
            path = DATA / key / f"{cfg['id']}.json"
            if not path.exists():
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if not (record.get("offerings") or []):
                continue
            bodies = fetch_bodies(cfg, key)
            if not bodies:
                print(f"  跳过 {key}/{cfg['id']}: 无法抓取", flush=True)
                continue
            cache[f"{key}/{cfg['id']}"] = bodies
            total = sum(len(body) for body in bodies)
            print(f"  已缓存 {key}/{cfg['id']} ({total} 字符, "
                  f"{len(bodies)} 个来源)", flush=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def excerpt_for(bodies, budget):
    """与 _fetch_generation_text 完全一致的语义: 先去重, 再按体积判预算。"""
    prepared = [run._dedupe_repeated_lines(body) for body in bodies]
    total = sum(len(body) for body in prepared)
    if total <= budget:
        return "\n".join(prepared)
    return "\n".join(
        run._generation_source_excerpt(
            body, max(1, int(budget * len(body) / total)))
        for body in prepared)


def coverage(excerpt, offerings):
    lowered = excerpt.lower()
    names = prices = name_hit = price_hit = 0
    for offering in offerings:
        name = (offering.get("name") or "").strip()
        if name:
            names += 1
            if name.lower() in lowered:
                name_hit += 1
        for field in ("price_per_image", "price_per_second",
                      "price_per_million"):
            value = offering.get(field)
            if isinstance(value, (int, float)) and value:
                prices += 1
                text = (f"{value:g}" if value >= 1
                        else f"{value:.4f}".rstrip("0"))
                if text in excerpt:
                    price_hit += 1
    return name_hit, names, price_hit, prices


def main():
    print(f"被测模块: {run.__file__}")
    print(f"默认预算: {run.GENERATION_EVIDENCE_BUDGET}")
    if os.environ.get("REBUILD_CACHE") == "1" or not CACHE.exists():
        print("抓取并缓存网页正文...", flush=True)
        cache = build_cache()
    else:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"\n缓存 {len(cache)} 家, 预算列 {BUDGETS}\n")

    header = f"{'provider':<24}{'原文':>8} " + " ".join(
        f"{budget // 1000:>4}k" for budget in BUDGETS)
    print(header)
    print("-" * len(header))
    for name in sorted(cache):
        key, pid = name.split("/")
        record = json.loads(
            (DATA / key / f"{pid}.json").read_text(encoding="utf-8"))
        offerings = record.get("offerings") or []
        bodies = cache[name]
        raw = sum(len(body) for body in bodies)
        cells = []
        for budget in BUDGETS:
            excerpt = excerpt_for(bodies, budget)
            name_hit, names, price_hit, prices = coverage(excerpt, offerings)
            cells.append(f"{len(excerpt) // 1000}k 名{name_hit}/{names} "
                         f"价{price_hit}/{prices}")
        print(f"{name:<24}{raw:>8} " + " ".join(cells))


if __name__ == "__main__":
    main()
