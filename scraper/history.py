"""数据持久化: 厂商价格记录、价格变动历史、公告、元信息。

所有 JSON 原子写入(tmp + rename), CI 中途被杀不会留下损坏的文件。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PROVIDERS_DIR = DATA / "providers"
NEWS_DIR = DATA / "news"
PLANS_DIR = DATA / "plans"
WEBSEARCH_DIR = DATA / "websearch"
CHANGES_FILE = DATA / "changes.json"
META_FILE = DATA / "meta.json"

MAX_CHANGES = 400          # 价格变动历史最多保留条数
MAX_NEWS_PER_PROVIDER = 40  # 每厂商公告最多保留条数

PRICE_FIELDS = ("input_per_1m", "output_per_1m", "cached_input_per_1m", "currency")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def load_provider(pid: str) -> Optional[dict]:
    return _load_json(PROVIDERS_DIR / f"{pid}.json", None)


def save_provider(pid: str, record: dict) -> None:
    _atomic_write_json(PROVIDERS_DIR / f"{pid}.json", record)


def load_news(pid: str) -> dict:
    return _load_json(NEWS_DIR / f"{pid}.json", {"entries": []})


def save_news(pid: str, record: dict) -> None:
    _atomic_write_json(NEWS_DIR / f"{pid}.json", record)


def load_plans(pid: str) -> dict:
    return _load_json(PLANS_DIR / f"{pid}.json", {"plans": []})


def save_plans(pid: str, record: dict) -> None:
    _atomic_write_json(PLANS_DIR / f"{pid}.json", record)


def load_websearch(pid: str) -> dict:
    return _load_json(WEBSEARCH_DIR / f"{pid}.json", {"offerings": []})


def save_websearch(pid: str, record: dict) -> None:
    _atomic_write_json(WEBSEARCH_DIR / f"{pid}.json", record)


def load_changes() -> list:
    return _load_json(CHANGES_FILE, [])


def append_changes(new_items: list) -> None:
    if not new_items:
        return
    changes = load_changes()
    changes.extend(new_items)
    _atomic_write_json(CHANGES_FILE, changes[-MAX_CHANGES:])


def load_meta() -> dict:
    return _load_json(META_FILE, {})


def save_meta(meta: dict) -> None:
    _atomic_write_json(META_FILE, meta)


def model_key(name: str) -> str:
    """模型名归一化, 用于跨次对比: 去空白/连字符, 小写。"""
    return re.sub(r"[\s\-_·.()（）]+", "", name or "").lower()


def diff_models(old: list, new: list) -> list:
    """对比新旧模型价格列表, 返回变动明细(不含时间戳, 由调用方补 ts)。

    只在旧数据本身来自 Claude 抽取时调用(种子数据 -> 首次抽取不算变动,
    避免初始化噪音)。
    """
    old_map = {model_key(m.get("model", "")): m for m in old}
    new_map = {model_key(m.get("model", "")): m for m in new}
    records = []

    for key, m in new_map.items():
        prev = old_map.get(key)
        if prev is None:
            records.append({
                "kind": "new",
                "model": m.get("model", ""),
                "fields": [
                    {"field": f, "old": None, "new": m.get(f)}
                    for f in ("input_per_1m", "output_per_1m")
                    if m.get(f) is not None
                ],
            })
            continue
        fields = []
        for f in PRICE_FIELDS:
            if prev.get(f) != m.get(f):
                fields.append({"field": f, "old": prev.get(f), "new": m.get(f)})
        if (prev.get("note") or None) != (m.get("note") or None):
            fields.append({"field": "note", "old": prev.get("note"),
                           "new": m.get("note")})
        if fields:
            records.append({"kind": "change", "model": m.get("model", ""),
                            "fields": fields})

    for key, m in old_map.items():
        if key not in new_map:
            records.append({"kind": "removed", "model": m.get("model", ""),
                            "fields": []})
    return records
