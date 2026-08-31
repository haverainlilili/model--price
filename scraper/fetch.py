"""网页抓取层: 拉取价格/公告页面并转成对 LLM 友好的纯文本。

- 浏览器 UA + 跟随重定向 + 两次重试, 应对多数官网的 UA 过滤
- HTML 转文本时保留表格结构(单元格 ' | ' 分隔), 这是价格页信息密度最高的部分
- markdown 响应(如 platform.claude.com 的 .md 文档)原样返回
"""
from __future__ import annotations

import os
import re
import shutil
import time
from html.parser import HTMLParser
from pathlib import Path

import requests

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TIMEOUT = 30
MAX_TEXT_CHARS = 250_000


class FetchError(RuntimeError):
    """抓取最终失败(网络/HTTP 状态码), 调用方保留旧数据即可。"""


class _TextExtractor(HTMLParser):
    """HTML -> 纯文本: 块级标签换行, 表格单元格用 ' | ' 分隔, 丢弃 script/style。"""

    _SKIP = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
    _BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "tr", "br", "ul", "ol", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
        text = "".join(parser.parts)
    except Exception:
        # HTMLParser 对畸形标签容错有限, 兜底做一次粗暴剥离
        text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _clean(text: str) -> str:
    text = text.replace("​", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


def _find_chrome_executable() -> str | None:
    """优先复用系统 Chrome；CI 找不到时交给 Playwright 使用自带 Chromium。"""
    configured = (os.environ.get("CHROME_EXECUTABLE") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise FetchError(f"CHROME_EXECUTABLE 不可执行: {path}")

    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    for command in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(command)
        if found:
            return found
    return None


def _chrome_launch_options() -> dict:
    """生成渲染浏览器参数；禁用 QUIC 规避部分站点的间歇性连接关闭。"""
    options = {"headless": True, "args": ["--disable-quic"]}
    executable = _find_chrome_executable()
    if executable:
        options["executable_path"] = executable
    return options


def _render_once(url: str, wait_ms: int) -> str:
    """执行一次浏览器渲染；重试策略由 fetch_rendered 统一处理。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise FetchError("playwright 未安装, 无法渲染 JS 页面") from exc
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**_chrome_launch_options())
            try:
                ctx = browser.new_context(user_agent=DEFAULT_UA, locale="zh-CN")
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass  # 有些站永远有长连接, networkidle 等不到
                page.wait_for_timeout(wait_ms)
                text = page.inner_text("body")
            finally:
                browser.close()
        if not text or not text.strip():
            raise FetchError("渲染后页面为空")
        return _clean(text)
    except FetchError:
        raise
    except Exception as exc:
        raise FetchError(f"渲染失败: {exc}") from exc


def fetch_rendered(url: str, wait_ms: int = 6000, retries: int = 2) -> str:
    """用无头浏览器渲染页面并返回可见文本，瞬时导航失败会有限重试。

    playwright 未安装或全部尝试失败时抛 FetchError，上层保留旧数据。
    """
    last_err: FetchError = FetchError("unknown")
    for attempt in range(retries + 1):
        try:
            return _render_once(url, wait_ms)
        except FetchError as exc:
            last_err = exc
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise last_err


def fetch(url: str, retries: int = 2) -> str:
    """抓取 url 并返回纯文本。失败重试, 最终失败抛 FetchError。"""
    headers = {
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,text/markdown,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last_err: Exception = FetchError("unknown")
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=TIMEOUT,
                                allow_redirects=True)
            if resp.status_code == 200:
                # 响应头没给 charset 时 requests 会猜 ISO-8859-1, 中文页会乱码
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                body = resp.text
                ctype = resp.headers.get("content-type", "").lower()
                looks_html = "html" in ctype or body[:200].lstrip().startswith("<")
                return _clean(html_to_text(body) if looks_html else body)
            last_err = FetchError(f"HTTP {resp.status_code}")
        except requests.RequestException as exc:
            last_err = exc
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    raise FetchError(str(last_err))
