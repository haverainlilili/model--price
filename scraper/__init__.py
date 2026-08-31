"""llm-price-tracker: 大模型 API 官网价格自动抓取、Claude 结构化抽取与站点生成。"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """读取项目根目录的 .env(若存在), 只填充尚未设置的环境变量。

    已在 shell 里 export 的值优先于 .env; .env 里的空值跳过。
    远程 CI 不用 .env, 由 GitHub Actions Secrets 注入。
    """
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val


_load_dotenv()
