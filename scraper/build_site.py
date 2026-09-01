"""静态站点生成: data/ 下的 JSON -> site/index.html + site/data.json。

零前端依赖: 单文件 HTML + 内联 CSS/JS + 系统字体(中文网络环境友好,
不加载任何 webfont/CDN 脚本)。

设计: 「行情终端 × 账本」。冷调纸底 + 墨蓝正文; 朱红/松绿只用于编码
价格涨跌(中文行情惯例: 红涨绿跌), 不做装饰色; 所有价格/时间/模型名走
等宽字体。签名元素是页面最顶部的深色滚动行情条, 由价格变动流水驱动。
"""
from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .history import ROOT, load_changes, load_meta, load_news, load_provider

SITE_DIR = ROOT / "site"
UTC8 = timezone(timedelta(hours=8))
REPO_URL = "https://github.com/haverainlilili/model--price"

FIELD_LABEL = {
    "input_per_1m": "输入价",
    "output_per_1m": "输出价",
    "cached_input_per_1m": "缓存输入价",
    "currency": "币种",
    "note": "备注",
}
CUR_SYMBOL = {"USD": "$", "CNY": "¥", "EUR": "€"}


def _e(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _fmt(v) -> str:
    """数字格式化: 去掉多余的 0 (2.0 -> 2, 0.50 -> 0.5)。"""
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _e(v)
    if f == int(f) and abs(f) < 1_000_000:
        return str(int(f))
    return f"{f:.3f}".rstrip("0").rstrip(".")


def _sym(cur: str | None) -> str:
    return CUR_SYMBOL.get((cur or "").upper(), "")


def _t(ts: str, fmt: str = "%m-%d %H:%M") -> str:
    """ISO UTC 时间 -> 北京时间显示。解析失败返回原文。"""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).astimezone(UTC8)
        return dt.strftime(fmt)
    except (TypeError, ValueError):
        return _e(ts or "")


def _safe_url(u) -> str | None:
    if u and str(u).startswith(("http://", "https://")):
        return _e(str(u))
    return None


def _price_cell(v, cur: str | None, rate: float) -> str:
    """价格单元格: 原币/折算人民币两个 span, 由 body[data-currency] 切换。

    CNY 原生或无法折算的只渲染一个 span(始终可见)。
    """
    if v is None:
        return '<span class="price">—</span>'
    sym = _sym(cur)
    orig = f"{sym}{_fmt(v)}"
    zero = ' price-zero' if (isinstance(v, (int, float)) and float(v) == 0) else ""
    conv = None
    if (cur or "").upper() == "USD" and rate:
        conv = f"≈¥{_fmt(round(float(v) * rate, 2))}"
    if conv:
        return (f'<span class="price p-orig{zero}">{_e(orig)}</span>'
                f'<span class="price p-cny{zero}">{_e(conv)}</span>')
    return f'<span class="price{zero}">{_e(orig)}</span>'


# ---------------------------------------------------------------- 模板块

CSS = """
:root{
  --bg:#F3F2EC; --panel:#FFFEFB; --panel2:#F8F7F1;
  --ink:#17231E; --ink2:#66716C; --ink3:#8D9691;
  --line:#DDE0D9; --line2:#C8CEC5;
  --accent:#0A6E59; --accent-dark:#075746; --accent-bg:#E4F2EC;
  --up:#B53A2E; --up-bg:#F9E9E5;
  --down:#087352; --down-bg:#E2F1EA;
  --new:#17649A; --new-bg:#E5EFF6;
  --seed:#84620A; --seed-bg:#F8F0D2;
  --err:#A32F21; --err-bg:#F9E7E3;
  --chart-in:#2878C7; --chart-out:#E56B37;
  --shadow:0 16px 38px rgba(36,48,42,.07);
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 var(--sans)}
a{color:var(--accent-dark);text-decoration-thickness:1px;text-underline-offset:3px}
a:hover{text-decoration:underline}
button,a{-webkit-tap-highlight-color:transparent}
:focus-visible{outline:3px solid rgba(10,110,89,.34);outline-offset:3px;border-radius:6px}
.skip-link{position:fixed;left:16px;top:12px;z-index:100;transform:translateY(-180%);
  padding:10px 14px;background:var(--panel);border:1px solid var(--line2);
  border-radius:8px;color:var(--ink);font-weight:700;box-shadow:var(--shadow)}
.skip-link:focus{transform:translateY(0)}
.wrap{max-width:1240px;margin:0 auto;padding:0 28px}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)} .neu{color:var(--ink2)}
.strike{text-decoration:line-through;color:var(--ink2)}

/* ---- 顶部行情条 ---- */
.ticker{display:flex;align-items:stretch;background:#12211B;border-bottom:1px solid #2B3E36}
.ticker-label{flex:none;display:flex;align-items:center;gap:8px;padding:0 20px;
  font:700 10px/1 var(--mono);letter-spacing:.22em;color:#D4E7DF;
  border-right:1px solid #30433B;background:#0A6E59}
.ticker-label::before{content:"";width:6px;height:6px;border-radius:50%;background:#B7F4DA;
  box-shadow:0 0 0 4px rgba(183,244,218,.1)}
.ticker-view{flex:1;overflow:hidden}
.ticker-track{display:inline-flex;align-items:center;white-space:nowrap;
  padding:11px 0;animation:tk 72s linear infinite;will-change:transform}
.ticker-copy{display:inline-flex}
.ticker:hover .ticker-track{animation-play-state:paused}
@keyframes tk{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.chip{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-size:11.5px;color:#B7C8C0;margin-right:42px}
.chip::before{content:"/";color:#557167;margin-right:12px}
.chip b{color:#F7FBF9;font-weight:650}
.chip .up{color:#FF9D8E}.chip .down{color:#65D0A5}.chip .neu{color:#A9BBB3}

/* ---- 报头 ---- */
.masthead{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(360px,.65fr);
  gap:72px;align-items:end;padding:68px 0 44px}
.intro{min-width:0}
.brand-line{display:flex;align-items:center;gap:12px;margin-bottom:26px;flex-wrap:wrap}
.brand-mark{display:grid;place-items:center;width:32px;height:32px;border-radius:9px;
  background:var(--ink);color:#fff;font:700 18px/1 var(--mono)}
.eyebrow{font:700 11px/1 var(--mono);letter-spacing:.19em;color:var(--ink2);
  text-transform:uppercase;margin:0}
.live-pill{display:inline-flex;align-items:center;gap:7px;margin-left:4px;padding:5px 9px;
  border:1px solid #BFD7CC;border-radius:999px;background:var(--accent-bg);
  color:var(--accent-dark);font:700 10px/1 var(--mono);letter-spacing:.05em}
.live-pill i{width:6px;height:6px;border-radius:50%;background:var(--accent)}
h1{max-width:780px;margin:0;font-size:clamp(46px,6.3vw,78px);font-weight:760;
  line-height:.98;letter-spacing:-.055em}
h1 span{color:var(--accent)}
.sub{max-width:700px;margin:24px 0 0;color:var(--ink2);font-size:15px;line-height:1.8}
.header-actions{display:flex;align-items:center;gap:20px;margin-top:30px;flex-wrap:wrap}
.header-actions a{display:inline-flex;align-items:center;justify-content:center;min-height:44px;
  font-weight:700;text-decoration:none}
.primary-action{padding:0 17px;border-radius:9px;background:var(--ink);color:#fff}
.primary-action:hover{background:var(--accent-dark);text-decoration:none}
.secondary-action{border-bottom:1px solid var(--line2)}
.spec{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:0;
  padding:10px;background:#172820;color:#fff;border-radius:18px;box-shadow:var(--shadow)}
.metric{min-width:0;margin:0;padding:14px 15px;border:1px solid rgba(255,255,255,.1);
  border-radius:10px;background:rgba(255,255,255,.035)}
.metric-wide{grid-column:1/-1}
.metric dt{margin:0 0 5px;color:#9CB0A7;font:650 10px/1.3 var(--mono);
  letter-spacing:.08em;text-transform:uppercase}
.metric dd{margin:0;color:#F7FAF8;font:650 14px/1.4 var(--mono);
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.metric-wide dd{font-size:16px}
.metric .b-warn{vertical-align:1px}

/* ---- 吸顶导航与控制 ---- */
.controls{position:sticky;top:0;z-index:20;display:flex;align-items:center;
  justify-content:space-between;gap:18px;margin:0 0 24px;padding:11px 0;
  background:rgba(243,242,236,.92);backdrop-filter:blur(14px);
  border-bottom:1px solid rgba(200,206,197,.7)}
.jump-nav{display:flex;align-items:center;gap:4px}
.jump-nav a{padding:8px 10px;border-radius:7px;color:var(--ink2);font-size:12.5px;
  font-weight:650;text-decoration:none;white-space:nowrap}
.jump-nav a:hover{background:var(--panel);color:var(--ink);text-decoration:none}
.control-groups{display:flex;align-items:center;justify-content:flex-end;gap:8px;min-width:max-content}
.control-label{margin-right:3px;color:var(--ink3);font:700 10px var(--mono);
  letter-spacing:.1em;text-transform:uppercase}
.seg{display:inline-flex;background:var(--panel);border:1px solid var(--line2);
  border-radius:10px;padding:3px;gap:2px;box-shadow:0 1px 0 rgba(25,35,30,.04)}
.seg button{min-height:34px;border:0;background:transparent;font:650 12px/1 var(--sans);
  color:var(--ink2);padding:0 12px;border-radius:7px;cursor:pointer;white-space:nowrap}
.seg button:hover{color:var(--ink);background:var(--panel2)}
.seg button.on{background:var(--ink);color:#fff;box-shadow:0 2px 7px rgba(23,35,30,.2)}

/* ---- 各厂商最低价柱状图 ---- */
.lowest{scroll-margin-top:76px;background:var(--panel);border:1px solid var(--line);
  border-radius:18px;margin:0 0 18px;overflow:hidden;box-shadow:var(--shadow)}
.lowest-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  padding:20px 22px;border-bottom:1px solid var(--line);background:var(--panel2)}
.lowest-kicker{margin:0 0 4px;color:var(--accent);font:700 9.5px/1.3 var(--mono);
  letter-spacing:.15em;text-transform:uppercase}
.lowest-title{margin:0;font-size:19px;line-height:1.35;letter-spacing:-.02em}
.lowest-desc{max-width:470px;margin:0;color:var(--ink2);font-size:11.5px;text-align:right}
.lowest-scroll{overflow-x:auto;scrollbar-color:var(--line2) transparent}
.lowest-plot{display:flex;align-items:stretch;gap:8px;min-width:980px;padding:22px 18px 16px}
.lowest-col{display:grid;grid-template-rows:24px 184px 34px 54px;flex:1 0 74px;min-width:0;
  text-align:center}
.lowest-amount{align-self:start;color:var(--ink);font:700 10.5px/1 var(--mono);
  font-variant-numeric:tabular-nums;white-space:nowrap}
.lowest-barbox{display:flex;align-items:flex-end;justify-content:center;margin:7px 8px 9px;
  border-bottom:1px solid var(--line2);background:repeating-linear-gradient(to top,
  transparent 0,transparent calc(25% - 1px),rgba(221,224,217,.58) 25%)}
.lowest-bar{width:min(52px,66%);height:max(8px,var(--bar-height));background:var(--accent);
  border-radius:6px 6px 1px 1px;box-shadow:inset 0 1px rgba(255,255,255,.2)}
.lowest-provider{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;
  -webkit-line-clamp:2;color:var(--ink);font-size:12px;font-weight:750;line-height:1.3}
.lowest-model-wrap{display:flex;flex-direction:column;align-items:center;gap:3px;min-width:0;
  padding:2px 2px 0}
.lowest-model{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;
  -webkit-line-clamp:2;color:var(--ink);font:700 11.5px/1.3 var(--mono);word-break:break-word}
.lowest-variant{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;padding:2px 5px;border-radius:4px;background:var(--accent-bg);
  color:var(--accent-dark);font:700 9px/1.2 var(--sans)}
.lowest-foot{margin:0;padding:11px 20px;border-top:1px solid var(--line);background:var(--panel2);
  color:var(--ink2);font-size:11.5px}

/* ---- 价格速览图 ---- */
.quick{scroll-margin-top:76px;background:var(--panel);border:1px solid var(--line);
  border-radius:18px;margin:0 0 76px;overflow:hidden;box-shadow:var(--shadow)}
.quick-head{display:flex;align-items:center;justify-content:space-between;gap:18px;
  padding:20px 22px;border-bottom:1px solid var(--line);background:var(--panel2)}
.quick-title{font-size:17px;line-height:1.35;font-weight:730;margin:0;letter-spacing:-.01em}
.quick-title::before{content:"01";display:inline-grid;place-items:center;width:27px;height:27px;
  margin-right:10px;border-radius:8px;background:var(--accent);color:#fff;
  font:700 10px/1 var(--mono);vertical-align:2px}
.blegend{display:inline-flex;gap:13px;align-items:center;justify-content:flex-end;
  color:var(--ink2);font-size:11.5px;flex-wrap:wrap}
.sw{display:inline-block;width:17px;height:7px;border-radius:99px;
  margin-right:6px;vertical-align:1px}
.sw-in{background:var(--chart-in)}.sw-out{background:var(--chart-out)}
.bnote{padding-right:4px;color:var(--ink3);font-family:var(--mono)}
.seg-scale button{min-height:30px;padding:0 10px;font-size:11px}
.chart-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;
  padding:18px}
.bgroup{min-width:0;padding:10px 12px 11px;border:1px solid var(--line);
  border-radius:12px;background:#FFF}
.brow{display:grid;grid-template-columns:minmax(132px,176px) minmax(0,1fr);gap:10px;
  align-items:center;padding:4px 0}
.bprov{display:flex;align-items:center;justify-content:space-between;gap:10px;
  min-height:30px;margin-bottom:4px;padding-bottom:7px;border-bottom:1px solid var(--line);
  font-weight:720;font-size:13px}
.btag{flex:none;font:650 9px var(--mono);color:var(--ink3);letter-spacing:.08em}
.bmodel{display:flex;align-items:center;gap:5px;min-width:0;font-family:var(--mono);
  font-size:10.5px;color:var(--ink2)}
.bmodel-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bvariant{flex:none;max-width:74px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  padding:2px 5px;border-radius:4px;background:var(--accent-bg);color:var(--accent-dark);
  font:700 8.5px/1.2 var(--sans)}
.bbars{display:flex;flex-direction:column;margin-right:64px;border-left:1px solid var(--line2);
  background:repeating-linear-gradient(to right,transparent 0,transparent calc(25% - 1px),
  rgba(221,224,217,.55) 25%)}
.bbar{position:relative;height:9px;border-radius:0 99px 99px 0;min-width:2px;
  width:var(--wl,0);transition:width .28s ease}
body[data-scale=lin] .bbar{width:var(--wi,0)}
.b-in{background:var(--chart-in);margin-bottom:3px}
.b-out{background:var(--chart-out)}
.b-none{background:none}
.bbar i{position:absolute;left:100%;top:50%;transform:translateY(-51%);
  padding-left:5px;color:var(--ink);font:650 9.5px/1 var(--mono);font-style:normal;
  font-variant-numeric:tabular-nums;white-space:nowrap}
.bfoot{margin:0;padding:12px 20px;border-top:1px solid var(--line);background:var(--panel2);
  color:var(--ink2);font-size:11.5px}

/* ---- 区块标题 ---- */
section.block{scroll-margin-top:76px;margin:0 0 88px}
.section-head{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,.7fr);
  gap:32px;align-items:end;margin-bottom:22px;padding-bottom:18px;border-bottom:1px solid var(--line2)}
.sec-eyebrow{font:700 10px var(--mono);letter-spacing:.18em;color:var(--accent);
  text-transform:uppercase;margin:0 0 5px}
h2.sec-title{margin:0;font-size:clamp(25px,3vw,34px);line-height:1.15;letter-spacing:-.035em}
.sec-sub{margin:0;color:var(--ink2);font-size:13px;text-align:right}

/* ---- 厂商价格块 ---- */
.prov{background:var(--panel);border:1px solid var(--line);border-radius:15px;
  margin:0 0 16px;overflow:hidden;box-shadow:0 4px 14px rgba(36,48,42,.025)}
.prov-head{display:flex;justify-content:space-between;align-items:center;gap:16px;
  min-height:64px;padding:13px 18px;flex-wrap:wrap;background:var(--panel2);
  list-style:none;cursor:pointer;user-select:none}
.prov-head::-webkit-details-marker{display:none}
.prov-head::marker{content:""}
.prov[open] .prov-head{border-bottom:1px solid var(--line)}
.prov-head:hover{background:#F4F5EF}
.prov-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.prov-title::before{content:"";width:8px;height:8px;border-radius:2px;background:var(--accent)}
.prov-title h3{margin:0;font-size:15.5px;letter-spacing:-.01em}
.tag{font:650 9.5px var(--mono);letter-spacing:.07em;padding:3px 7px;border-radius:5px}
.tag-region{background:#EEF0EB;color:var(--ink2);border:1px solid var(--line)}
.badge{font-size:10px;padding:3px 8px;border-radius:999px;font-weight:700}
.b-seed{background:var(--seed-bg);color:var(--seed)}
.b-err{background:var(--err-bg);color:var(--err)}
.b-warn{background:#EEF0EB;color:var(--ink2)}
.prov-meta{display:flex;gap:14px;align-items:center;flex-wrap:wrap;color:var(--ink2);
  font:500 10.5px var(--mono);font-variant-numeric:tabular-nums}
.prov-toggle{display:inline-flex;align-items:center;gap:7px;color:var(--accent-dark);
  font-weight:750;white-space:nowrap}
.toggle-close{display:none}
.prov[open] .toggle-open{display:none}
.prov[open] .toggle-close{display:inline}
.prov-chevron{width:7px;height:7px;border-right:1.5px solid currentColor;
  border-bottom:1.5px solid currentColor;transform:rotate(45deg);transition:transform .18s ease}
.prov[open] .prov-chevron{transform:rotate(225deg)}
.prov-source{display:flex;justify-content:flex-end;padding:8px 18px;border-bottom:1px solid var(--line);
  background:#FCFCF8;font-size:11.5px}
.prov-source a{font-weight:700}
.promo{margin:0;padding:10px 18px;background:var(--up-bg);color:#7C2E1F;
  font-size:12.5px;border-bottom:1px solid #EACFC8}
.promo b{font:700 9.5px var(--mono);letter-spacing:.16em;color:var(--up);margin-right:8px}

/* ---- 价格表 ---- */
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px;min-width:720px}
thead th{background:#FCFCF8;color:var(--ink2);text-align:right;padding:11px 14px;
  border-bottom:1px solid var(--line2);font:700 9.5px var(--mono);
  letter-spacing:.09em;text-transform:uppercase;white-space:nowrap}
thead th:first-child{text-align:left}
thead th.c-note-h{text-align:left}
tbody td{padding:10px 14px;border-bottom:1px solid var(--line);text-align:right;
  vertical-align:top;transition:background .15s ease}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(even){background:#FAFAF6}
tbody tr:hover td{background:#F1F6F2}
td.c-model{font-family:var(--mono);font-size:11.5px;font-weight:650;text-align:left;
  color:var(--ink);word-break:break-all}
td.c-note{text-align:left;font-size:11.5px;color:var(--ink2);max-width:360px}
.price{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:650;white-space:nowrap}
.price-zero{color:var(--down);font-weight:750}
.empty-row td{text-align:left;padding:18px;color:var(--ink2);font-size:12px}
body[data-currency=orig] .p-cny{display:none}
body[data-currency=cny] .p-orig{display:none}

/* ---- 变动流水 ---- */
.chg-list{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.chg{position:relative;display:grid;grid-template-columns:100px 128px 1fr;gap:5px 16px;
  align-items:baseline;background:var(--panel);border:1px solid var(--line);
  border-left:4px solid var(--line2);border-radius:11px;padding:12px 16px}
.chg-up{border-left-color:var(--up)}.chg-down{border-left-color:var(--down)}
.chg-new{border-left-color:var(--new)}.chg-removed{border-left-color:var(--ink3)}
.chg-time{color:var(--ink3);font:550 10.5px var(--mono);font-variant-numeric:tabular-nums}
.chg-prov{font-weight:720;font-size:12.5px}
.chg-body{font-size:12.5px}.chg-body .m{font:650 11.5px var(--mono)}
.arrow{font-weight:750}
.blank{background:var(--panel);border:1px dashed var(--line2);border-radius:13px;
  padding:28px;color:var(--ink2);font-size:13px;text-align:center}

/* ---- 公告 ---- */
.news-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;align-items:start}
.news-card{background:var(--panel);border:1px solid var(--line);border-radius:14px;
  padding:16px 17px;box-shadow:0 4px 14px rgba(36,48,42,.025)}
.news-card h3{margin:0 0 8px;font-size:14.5px;display:flex;justify-content:space-between;
  align-items:baseline;gap:10px}
.news-card h3 a{color:var(--ink3);font:700 9.5px var(--mono);letter-spacing:.06em;white-space:nowrap}
.news-card ul{list-style:none;margin:0;padding:0}
.n-item{padding:10px 0;border-top:1px solid var(--line)}
.n-date{display:inline-block;margin-right:6px;padding:2px 5px;border-radius:4px;
  background:#EEF0EB;color:var(--ink2);font:650 9.5px var(--mono);vertical-align:1px}
.n-title{font-weight:680;font-size:12.5px;line-height:1.55}
.n-summary{margin:5px 0 0;color:var(--ink2);font-size:11.5px;line-height:1.65}
.news-empty{color:var(--ink2);font-size:11.5px;padding:10px 0 2px}

/* ---- 页脚 ---- */
footer{margin:0 0 32px;padding:24px 26px;border-radius:16px;background:#172820;
  color:#AFC0B8;font-size:11.5px;display:flex;justify-content:space-between;gap:28px;flex-wrap:wrap}
footer p{max-width:860px}footer a{color:#D8EFE6}footer .mono{font-size:10.5px}

/* ---- 筛选 ---- */
body[data-region=intl] .prov[data-region=domestic],
body[data-region=intl] .news-card[data-region=domestic],
body[data-region=intl] .bgroup[data-region=domestic],
body[data-region=intl] .lowest-col[data-region=domestic]{display:none}
body[data-region=domestic] .prov[data-region=intl],
body[data-region=domestic] .news-card[data-region=intl],
body[data-region=domestic] .bgroup[data-region=intl],
body[data-region=domestic] .lowest-col[data-region=intl]{display:none}

@media(hover:hover){
  .prov,.news-card{transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}
  .prov:hover,.news-card:hover{transform:translateY(-2px);border-color:var(--line2);
    box-shadow:0 12px 28px rgba(36,48,42,.07)}
}
@media(max-width:960px){
  .masthead{grid-template-columns:1fr;gap:34px;padding:52px 0 38px}
  .spec{grid-template-columns:repeat(4,minmax(0,1fr))}
  .metric-wide{grid-column:span 2}
  .chart-grid{grid-template-columns:1fr}
  .news-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media(max-width:760px){
  .wrap{padding:0 18px}
  .ticker-label{display:none}
  .masthead{padding-top:38px}
  h1{font-size:clamp(40px,13vw,58px)}
  .sub{margin-top:18px;font-size:14px}
  .spec{grid-template-columns:repeat(2,minmax(0,1fr))}
  .metric-wide{grid-column:1/-1}
  .controls{align-items:flex-start;margin-left:-18px;margin-right:-18px;padding:10px 18px;
    overflow-x:auto;scrollbar-width:none}
  .controls::-webkit-scrollbar{display:none}
  .jump-nav{display:none}
  .control-groups{justify-content:flex-start}
  .control-label{display:none}
  .seg button{min-height:44px;padding:0 13px}
  .lowest{margin-left:-2px;margin-right:-2px}
  .lowest-head{align-items:flex-start;flex-direction:column;gap:7px;padding:17px}
  .lowest-desc{text-align:left}
  .lowest-plot{padding-left:12px;padding-right:12px}
  .quick{margin-bottom:64px}
  .quick-head{align-items:flex-start;flex-direction:column;padding:17px}
  .blegend{justify-content:flex-start}
  .chart-grid{padding:10px}
  .brow{grid-template-columns:minmax(90px,116px) minmax(0,1fr);gap:8px}
  .bbars{margin-right:56px}
  .bfoot{padding:12px 15px}
  section.block{margin-bottom:68px}
  .section-head{grid-template-columns:1fr;gap:9px;margin-bottom:16px}
  .sec-sub{text-align:left}
  .prov-head{align-items:flex-start;flex-direction:column;gap:8px}
  table{min-width:680px}
  thead th:first-child,td.c-model{position:sticky;left:0;z-index:1;background:#FFFEFB;
    box-shadow:1px 0 0 var(--line)}
  tbody tr:nth-child(even) td.c-model{background:#FAFAF6}
  .chg{grid-template-columns:1fr;gap:2px;padding:12px 14px}
  .news-grid{grid-template-columns:1fr}
  footer{margin-left:-4px;margin-right:-4px;padding:22px}
}
@media(max-width:380px){
  .brand-line{gap:9px}.live-pill{margin-left:0}.spec{grid-template-columns:1fr}
  .metric-wide{grid-column:auto}.header-actions{align-items:stretch;flex-direction:column}
  .header-actions a{width:100%}.brow{grid-template-columns:88px minmax(0,1fr)}
}
@media(prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}.ticker-track{animation:none}.ticker-view{overflow-x:auto}
  .bbar,.prov,.news-card,.prov-chevron{transition:none}
}
"""

JS = """
(function(){
  var b=document.body;
  function setRegion(r){
    b.dataset.region=r;
    document.querySelectorAll('[data-region-btn]').forEach(function(x){
      var on=x.dataset.regionBtn===r;
      x.classList.toggle('on',on);x.setAttribute('aria-pressed',on);
    });
  }
  function setCur(c){
    b.dataset.currency=c;
    document.querySelectorAll('[data-cur-btn]').forEach(function(x){
      var on=x.dataset.curBtn===c;
      x.classList.toggle('on',on);x.setAttribute('aria-pressed',on);
    });
    try{localStorage.setItem('lpw-cur',c)}catch(e){}
  }
  document.querySelectorAll('[data-region-btn]').forEach(function(x){
    x.addEventListener('click',function(){setRegion(x.dataset.regionBtn)});
  });
  document.querySelectorAll('[data-cur-btn]').forEach(function(x){
    x.addEventListener('click',function(){setCur(x.dataset.curBtn)});
  });
  function setScale(s){
    if(s==='lin'){b.dataset.scale='lin'}else{delete b.dataset.scale}
    document.querySelectorAll('[data-scale-btn]').forEach(function(x){
      var on=x.dataset.scaleBtn===s;
      x.classList.toggle('on',on);x.setAttribute('aria-pressed',on);
    });
    try{localStorage.setItem('lpw-scale',s)}catch(e){}
  }
  document.querySelectorAll('[data-scale-btn]').forEach(function(x){
    x.addEventListener('click',function(){setScale(x.dataset.scaleBtn)});
  });
  try{var c=localStorage.getItem('lpw-cur');if(c==='orig'||c==='cny')setCur(c)}catch(e){}
  try{var s=localStorage.getItem('lpw-scale');if(s==='lin')setScale(s)}catch(e){}
})();
"""


# ---------------------------------------------------------------- 渲染

def _ticker_chips(changes: list, prov_names: dict, prov_cur: dict) -> str:
    chips = []
    for ch in reversed(changes[-10:]):
        pid = ch.get("provider", "")
        name = _e(prov_names.get(pid, pid))
        model = _e(ch.get("model", ""))
        sym = _sym(prov_cur.get(pid))
        kind = ch.get("kind")
        if kind == "new":
            vals = []
            for f in ("input_per_1m", "output_per_1m"):
                v = next((x["new"] for x in ch.get("fields", [])
                          if x["field"] == f), None)
                if v is not None:
                    vals.append(f"{sym}{_fmt(v)}")
            body = f'新模型 {" / ".join(vals)}' if vals else "新模型"
            chips.append(f'<span class="chip"><b>{name}</b> {model} · '
                         f'<span class="neu">{_e(body)}</span></span>')
        elif kind == "removed":
            chips.append(f'<span class="chip"><b>{name}</b> {model} · '
                         f'<span class="neu">已下架</span></span>')
        else:
            parts = []
            for f in ch.get("fields", []):
                label = FIELD_LABEL.get(f["field"], f["field"])
                old, new = f.get("old"), f.get("new")
                if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                    if new > old:
                        cls, arrow = "up", "▲"
                    elif new < old:
                        cls, arrow = "down", "▼"
                    else:
                        cls, arrow = "neu", "·"
                    parts.append(f'{label} {_e(sym)}{_fmt(old)}→'
                                 f'<span class="{cls}">{_e(sym)}{_fmt(new)}</span> '
                                 f'<span class="{cls}">{arrow}</span>')
                else:
                    parts.append(f'{label} <span class="neu">变更</span>')
            if parts:
                chips.append(f'<span class="chip"><b>{name}</b> {model} · '
                             f'{"；".join(parts)}</span>')
    if not chips:
        return ""
    row = "".join(chips)
    # 内容渲染两遍, 配合 translateX(-50%) 实现无缝循环
    return (f'<div class="ticker" aria-label="最新价格变动">'
            f'<span class="ticker-label">LIVE</span>'
            f'<div class="ticker-view"><div class="ticker-track">{row}'
            f'<span class="ticker-copy" aria-hidden="true">{row}</span>'
            f'</div></div></div>')


def _quick_variant(note: str | None) -> str:
    """把同名模型的长备注压缩成速览区可读的价格档位标签。"""
    text = str(note or "").strip()
    if not text:
        return "不同档位"

    def compact_number(value: str) -> str:
        return value.rstrip("0").rstrip(".") if "." in value else value

    lower = text.lower()
    tier = ""
    if "批量" in text or "batch" in lower:
        tier = "批量"
    elif "flex" in lower:
        tier = "Flex"
    elif "优先级" in text or "优先服务" in text or "priority" in lower:
        tier = "优先"
    elif "standard" in lower or "标准" in text:
        tier = "标准"

    context = ""
    if "短上下文" in text:
        context = "短"
    elif "长上下文" in text:
        context = "长"

    band = ""
    range_match = re.search(
        r"输入长度\s*[\[\(]\s*(\d+(?:\.\d+)?)\s*,\s*"
        r"(\d+(?:\.\d+)?)\s*[\]\)](?:\s*千\s*tokens?)?",
        text,
        flags=re.IGNORECASE,
    )
    if range_match:
        start = compact_number(range_match.group(1))
        end = compact_number(range_match.group(2))
        band = f"{start}–{end}K"
    else:
        open_match = re.search(
            r"输入长度\s*\[\s*(\d+(?:\.\d+)?)\s*\+\s*\)", text)
        limit_match = re.search(
            r"([≤≥<>])\s*(\d+(?:\.\d+)?)\s*[kK]\s*输入", text)
        if open_match:
            band = f"{compact_number(open_match.group(1))}K+"
        elif limit_match:
            limit = compact_number(limit_match.group(2))
            band = f"{limit_match.group(1)}{limit}K"

    if context:
        return f"{tier}·{context}" if tier else f"{context}上下文"
    if band:
        return f"{tier}·{band}" if tier else band
    if "空闲时段" in text:
        return "空闲"
    if "高峰时段" in text:
        return "高峰"
    if tier:
        return tier

    first = re.split(r"[；;]", text, maxsplit=1)[0].strip()
    return first if len(first) <= 10 else first[:9] + "…"


def _cheapest_chart(providers_cfg: list, recs: dict, rate: float) -> str:
    """从每家价格页最前 4 条记录中选输入价与输出价合计最低的一条。"""
    picks = []
    for cfg in providers_cfg:
        rec = recs.get(cfg["id"]) or {}
        models = rec.get("models") or []
        name_counts: dict[str, int] = {}
        for model in models:
            model_name = str(model.get("model") or "")
            name_counts[model_name] = name_counts.get(model_name, 0) + 1
        choices = []
        for model in models[:4]:
            cur = (model.get("currency") or rec.get("currency") or "").upper()
            if cur not in {"CNY", "USD"}:
                continue
            fx = rate if cur == "USD" else 1.0
            prices = []
            converted = []
            for field in ("input_per_1m", "output_per_1m"):
                value = model.get(field)
                price = (float(value) * fx
                         if isinstance(value, (int, float)) and value >= 0 else None)
                converted.append(price)
                if price is not None:
                    prices.append(price)
            if not prices:
                continue
            model_name = str(model.get("model") or "未命名模型")
            note = str(model.get("note") or "").strip()
            variant = _quick_variant(note) if name_counts.get(model_name, 0) > 1 else ""
            choices.append((sum(prices), model_name, converted[0], converted[1],
                            variant, note))
        if choices:
            total, model_name, input_price, output_price, variant, note = min(
                choices, key=lambda item: item[0])
            picks.append((cfg, total, model_name, input_price, output_price,
                          variant, note))

    if not picks:
        return ""

    picks.sort(key=lambda item: item[1])
    highest = max(item[1] for item in picks)

    def price_label(value) -> str:
        return f"¥{_fmt(round(value, 2))}" if value is not None else "无报价"

    columns = []
    for cfg, total, model_name, input_price, output_price, variant, note in picks:
        region = "domestic" if cfg.get("region") == "国内" else "intl"
        provider = cfg.get("name_cn") or cfg.get("name") or cfg["id"]
        height = total / highest * 100 if highest > 0 else 0
        amount = f"¥{_fmt(round(total, 2))}"
        variant_detail = f"，档位 {variant}" if variant else ""
        detail = (f"{provider}，{model_name}{variant_detail}，输入加输出合计 {amount}；"
                  f"输入 {price_label(input_price)}，输出 {price_label(output_price)}")
        variant_html = (f'<span class="lowest-variant" title="{_e(note)}">'
                        f'{_e(variant)}</span>' if variant else "")
        columns.append(
            f'<div class="lowest-col" data-region="{region}" role="listitem" '
            f'aria-label="{_e(detail)}">'
            f'<div class="lowest-amount">{_e(amount)}</div>'
            f'<div class="lowest-barbox" aria-hidden="true">'
            f'<div class="lowest-bar" style="--bar-height:{height:.1f}%"></div></div>'
            f'<div class="lowest-provider" title="{_e(provider)}">{_e(provider)}</div>'
            f'<div class="lowest-model-wrap"><span class="lowest-model" '
            f'title="{_e(model_name)}">{_e(model_name)}</span>{variant_html}</div>'
            f'</div>')

    return (
        '<section class="lowest" id="lowest" aria-labelledby="lowest-title">'
        '<div class="lowest-head"><div><p class="lowest-kicker">LOWEST BY PROVIDER</p>'
        '<h2 class="lowest-title" id="lowest-title">各厂商最新 4 条中的最低价</h2></div>'
        '<p class="lowest-desc">每根柱代表一家厂商 · 从左到右按合计价由低到高 · '
        '统一折算人民币</p></div>'
        '<div class="lowest-scroll" role="region" tabindex="0" '
        'aria-label="各厂商最低价柱状图，可横向滚动">'
        f'<div class="lowest-plot" role="list">{"".join(columns)}</div></div>'
        f'<p class="lowest-foot">横轴为厂商，柱高按合计价线性比较；每家仅在官网价格页最前的 '
        f'4 条记录中选择；同名模型沿用价格速览中的档位摘要。缺少输入价或输出价时，'
        f'以已有单项价格参与比较 · '
        f'USD 按 1 USD ≈ ¥{_fmt(rate)} 折算。</p></section>')


def _quick_chart(providers_cfg: list, recs: dict, rate: float) -> str:
    """首页顶部价格速览: 每家厂商价格页最前的 4 个模型, 输入/输出双条。

    对数/线性双刻度同时渲染(条宽写进 CSS 变量 --wl/--wi, 按钮切换):
    线性刻度价格差异直观, 但跨数量级时低价模型被压成一条线; 对数刻度
    完整但压缩差异。两种互补, 读者自选。
    跨厂商可比的前提是同一币种: USD 统一按汇率折算成人民币。
    「最新 4 个」按各官网价格页的排列顺序取(各家都把最新模型放在最前)。
    """
    groups, vals = [], []
    for cfg in providers_cfg:
        rec = recs.get(cfg["id"]) or {}
        models = rec.get("models") or []
        name_counts: dict[str, int] = {}
        for model in models:
            name = str(model.get("model") or "")
            name_counts[name] = name_counts.get(name, 0) + 1
        rows = []
        for m in models[:4]:
            cur = (m.get("currency") or rec.get("currency") or "").upper()
            fx = rate if cur == "USD" else 1.0

            def cv(v, _fx=fx):
                return float(v) * _fx if isinstance(v, (int, float)) and v > 0 else None

            ci, co = cv(m.get("input_per_1m")), cv(m.get("output_per_1m"))
            if ci is None and co is None:
                continue
            model = str(m.get("model") or "")
            note = str(m.get("note") or "").strip()
            variant = _quick_variant(note) if name_counts.get(model, 0) > 1 else ""
            rows.append((model, ci, co, variant, note))
            vals += [v for v in (ci, co) if v]
        if rows:
            groups.append((cfg, rows))
    if not groups or not vals:
        return ""

    lo, hi = min(vals), max(vals)
    span = math.log10(hi) - math.log10(lo) if hi > lo else 1.0

    def w_log(v) -> str:
        if v is None:
            return "0"
        return f"{max(2.0, min(100.0, (math.log10(v) - math.log10(lo)) / span * 100)):.1f}"

    def w_lin(v) -> str:
        if v is None:
            return "0"
        return f"{max(1.5, v / hi * 100):.1f}"

    def vlabel(v) -> str:
        return f"¥{_fmt(round(v, 2))}" if v is not None else "—"

    parts = []
    for cfg, rows in groups:
        region = cfg.get("region", "")
        dr = "domestic" if region == "国内" else "intl"
        inner = []
        for model, ci, co, variant, note in rows:
            bars = (f'<div class="bbar b-in" style="--wl:{w_log(ci)}%;'
                    f'--wi:{w_lin(ci)}%"><i>{vlabel(ci)}</i></div>'
                    f'<div class="bbar b-out" style="--wl:{w_log(co)}%;'
                    f'--wi:{w_lin(co)}%"><i>{vlabel(co)}</i></div>')
            tag = (f'<span class="bvariant" title="{_e(note)}">{_e(variant)}</span>'
                   if variant else "")
            inner.append(f'<div class="brow"><span class="bmodel">'
                         f'<span class="bmodel-name">{_e(model)}</span>{tag}</span>'
                         f'<div class="bbars">{bars}</div></div>')
        parts.append(f'<div class="bgroup" data-region="{dr}">'
                     f'<div class="bprov">{_e(cfg.get("name_cn") or cfg["name"])}'
                     f'<span class="btag">{_e(region)}</span></div>'
                     f'{"".join(inner)}</div>')

    return (
        '<section class="quick" id="quick" aria-label="价格速览图">'
        '<div class="quick-head"><h2 class="quick-title">价格速览 · 每家最新的 4 个模型'
        '</h2><div class="blegend"><span><i class="sw sw-in"></i>输入</span>'
        '<span><i class="sw sw-out"></i>输出</span>'
        '<span class="bnote">统一折算人民币</span>'
        '<div class="seg seg-scale" role="group" aria-label="刻度切换">'
        '<button data-scale-btn="log" class="on" aria-pressed="true">对数刻度</button>'
        '<button data-scale-btn="lin" aria-pressed="false">线性刻度</button></div>'
        '</div></div>'
        f'<div class="chart-grid">{"".join(parts)}</div>'
        f'<p class="bfoot">线性刻度下价格差异直观, 但低价模型会被压扁; 对数刻度完整'
        f'但压缩差异 —— 右上角可切换 · USD 按 1 USD ≈ ¥{_fmt(rate)} 折算 · 每家取'
        '官网价格页最前的 4 个模型(即最新), 同名模型后的标签说明价格档位差异; '
        '完整价格与备注见下方明细表。</p></section>')


def _prov_section(cfg: dict, rec: dict | None, rate: float) -> str:
    pid = cfg["id"]
    name = cfg.get("name_cn") or cfg["name"]
    region = cfg.get("region", "")
    data_region = "domestic" if region == "国内" else "intl"
    rec = rec or {}
    models = rec.get("models") or []
    url = _safe_url(cfg.get("pricing_url")
                    or (cfg.get("pricing_urls") or [None])[0])
    cur = rec.get("currency")

    badges = []
    if rec.get("last_error"):
        badges.append('<span class="badge b-err" title="%s">抓取失败 · 显示上次数据</span>'
                      % _e(rec["last_error"][:160]))
    if rec.get("status_note"):
        badges.append(f'<span class="badge b-warn">{_e(rec["status_note"])}</span>')
    if rec.get("source") != "claude" and models:
        badges.append('<span class="badge b-seed" '
                      'title="首次成功抓取解析官网后自动替换">种子数据 · 待校准</span>')
    badge_html = " ".join(badges)

    fetched = rec.get("fetched_at")
    updated = f'更新于 {_t(fetched)}' if fetched else ""
    link = f'<a href="{url}" target="_blank" rel="noopener">官网价格页 ↗</a>' if url else ""
    count = f"{len(models)} 模型" if models else "暂无模型数据"
    meta_bits = [x for x in (count, updated) if x]
    source = f'<div class="prov-source">{link}</div>' if link else ""
    promo = ""
    if rec.get("promotions"):
        p = _e(str(rec["promotions"]).strip())
        if len(p) > 220:
            p = p[:220] + "…"
        promo = f'<p class="promo"><b>活动</b>{p}</p>'

    rows = []
    for m in models:
        note = _e(m.get("note") or "")
        rows.append(
            f'<tr><td class="c-model">{_e(m.get("model", ""))}</td>'
            f'<td>{_price_cell(m.get("input_per_1m"), m.get("currency") or cur, rate)}</td>'
            f'<td>{_price_cell(m.get("output_per_1m"), m.get("currency") or cur, rate)}</td>'
            f'<td>{_price_cell(m.get("cached_input_per_1m"), m.get("currency") or cur, rate)}</td>'
            f'<td class="c-note">{note}</td></tr>')
    if not rows:
        reason = (_e(rec.get("last_error") or "") or
                  "该页面暂未解析出价格表(可能需要登录或由前端脚本渲染)。")
        rows.append(f'<tr class="empty-row"><td colspan="5">暂无数据:{reason}'
                    f'管线每小时自动重试, 恢复后会自动出现在这里。</td></tr>')

    toggle = ('<span class="prov-toggle"><span class="toggle-open">展开价格</span>'
              '<span class="toggle-close">收起价格</span>'
              '<span class="prov-chevron" aria-hidden="true"></span></span>')
    return (f'<details class="prov" data-region="{data_region}" id="prov-{_e(pid)}">'
            f'<summary class="prov-head"><div class="prov-title"><h3>{_e(name)}</h3>'
            f'<span class="tag tag-region">{_e(region)}</span>{badge_html}</div>'
            f'<div class="prov-meta">{"".join(m and f"<span>{m}</span>" or "" for m in meta_bits)}'
            f'{toggle}</div></summary>{source}{promo}<div class="table-wrap"><table>'
            f'<thead><tr><th>模型</th><th>输入 / 百万tokens</th>'
            f'<th>输出 / 百万tokens</th><th>缓存输入</th>'
            f'<th class="c-note-h">备注</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>')


def _chg_item(ch: dict) -> str:
    pid = ch.get("provider", "")
    name = _e(ch.get("provider_name") or pid)
    model = _e(ch.get("model", ""))
    time = _t(ch.get("ts", ""))
    kind = ch.get("kind")
    cls = {"change": "chg-up", "new": "chg-new", "removed": "chg-removed"}
    # 方向取第一个数值字段: 涨红 / 跌绿 / 非数值中性
    for f in ch.get("fields", []):
        o, n = f.get("old"), f.get("new")
        if isinstance(o, (int, float)) and isinstance(n, (int, float)):
            if n < o:
                cls["change"] = "chg-down"
            break

    if kind == "new":
        vals = []
        for f in ch.get("fields", []):
            label = FIELD_LABEL.get(f["field"], f["field"])
            v = f.get("new")
            if v is not None and isinstance(v, (int, float)):
                vals.append(f"{label} <b>{_fmt(v)}</b>")
        body = f"新模型 · {' · '.join(vals)}" if vals else "新模型"
    elif kind == "removed":
        body = "已从官网价格页移除"
    else:
        parts = []
        for f in ch.get("fields", []):
            label = FIELD_LABEL.get(f["field"], f["field"])
            old, new = f.get("old"), f.get("new")
            if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                if new > old:
                    c, arrow = "up", "▲"
                elif new < old:
                    c, arrow = "down", "▼"
                else:
                    c, arrow = "neu", "·"
                pct = ""
                if isinstance(old, (int, float)) and old:
                    pct = f' <span class="{c} arrow">{arrow}{abs((new - old) / old) * 100:.1f}%</span>'
                parts.append(f'{label} <span class="strike">{_fmt(old)}</span>'
                             f' → <b class="{c}">{_fmt(new)}</b>{pct}')
            else:
                o_s = _e(str(old)[:80]) if old is not None else "—"
                n_s = _e(str(new)[:80]) if new is not None else "—"
                parts.append(f'{label} <span class="strike">{o_s}</span>'
                             f' → <span class="neu">{n_s}</span>')
        body = "；".join(parts)

    return (f'<li class="chg {cls.get(kind, "chg-up")}">'
            f'<span class="chg-time">{time}</span>'
            f'<span class="chg-prov">{name}</span>'
            f'<span class="chg-body"><span class="m">{model}</span> · {body}</span></li>')


def _news_card(cfg: dict, rec: dict) -> str:
    name = cfg.get("name_cn") or cfg["name"]
    region = cfg.get("region", "")
    data_region = "domestic" if region == "国内" else "intl"
    url = _safe_url(cfg.get("news_url"))
    link = (f'<a href="{url}" target="_blank" rel="noopener">公告页 ↗</a>'
            if url else "")
    items = []
    for e in (rec.get("entries") or [])[:6]:
        date = _e(e.get("date") or "")
        if not date and e.get("first_seen"):
            date = _t(e["first_seen"], "%m-%d")
        title = _e(e.get("title") or "")
        eurl = _safe_url(e.get("url"))
        if eurl:
            title = f'<a class="n-title" href="{eurl}" target="_blank" rel="noopener">{title}</a>'
        else:
            title = f'<span class="n-title">{title}</span>'
        summary = _e(e.get("summary") or "")
        sum_html = f'<p class="n-summary">{summary}</p>' if summary else ""
        date_html = f'<span class="n-date">{date}</span>' if date else ""
        items.append(f'<li class="n-item">{date_html}{title}{sum_html}</li>')
    body = ("".join(items) if items else
            '<li class="news-empty">尚未抓到公告条目, 每小时自动重试。</li>')
    return (f'<div class="news-card" data-region="{data_region}">'
            f'<h3>{_e(name)}{link}</h3><ul>{body}</ul></div>')


# ---------------------------------------------------------------- 主入口

def build(providers_cfg: list) -> Path:
    meta = load_meta()
    changes = load_changes()
    fx = meta.get("fx") or {}
    rate = float(fx.get("usd_cny") or 7.2)

    recs, news_recs, prov_names, prov_cur = {}, {}, {}, {}
    total_models = 0
    for cfg in providers_cfg:
        pid = cfg["id"]
        rec = load_provider(pid)
        recs[pid] = rec
        news_recs[pid] = load_news(pid)
        prov_names[pid] = cfg.get("name_cn") or cfg["name"]
        if rec:
            prov_cur[pid] = rec.get("currency")
            total_models += len(rec.get("models") or [])

    # ---- 报头
    gen = _t(meta.get("generated_at"), "%Y-%m-%d %H:%M")
    fx_src = fx.get("source") or "内置常数"
    fx_stale = ' <span class="badge b-warn">过期</span>' if fx.get("stale") else ""
    fx_line = f"1 USD ≈ ¥{_fmt(rate)}"
    ticker = _ticker_chips(changes, prov_names, prov_cur)

    spec = (f'<dl class="spec" aria-label="数据概览">'
            f'<div class="metric metric-wide"><dt>最近更新 · 北京时间</dt><dd>{gen}</dd></div>'
            f'<div class="metric"><dt>覆盖厂商</dt><dd>{len(providers_cfg)} 家</dd></div>'
            f'<div class="metric"><dt>在列模型</dt><dd>{total_models} 个</dd></div>'
            f'<div class="metric"><dt>变动记录</dt><dd>{len(changes)} 条</dd></div>'
            f'<div class="metric"><dt>实时汇率</dt><dd>{_e(fx_line)}{fx_stale}</dd></div>'
            f'<div class="metric metric-wide"><dt>汇率来源</dt><dd>{_e(fx_src)}</dd></div>'
            f'</dl>')

    masthead = (
        f'<header class="masthead"><div class="intro">'
        f'<div class="brand-line"><span class="brand-mark" aria-hidden="true">↗</span>'
        f'<p class="eyebrow">LLM PRICE WATCH</p>'
        f'<span class="live-pill"><i aria-hidden="true"></i>每小时更新</span></div>'
        f'<h1>大模型 API<br><span>价格看板</span></h1>'
        f'<p class="sub">把 {len(providers_cfg)} 家主流厂商的公开价格、变动与公告放进同一张可比较的账本。'
        f'支持人民币折算，时间统一为北京时间；实际价格以各厂商官网为准。</p>'
        f'<div class="header-actions"><a class="primary-action" href="#prices">查看完整价格 ↓</a>'
        f'<a class="secondary-action" href="{REPO_URL}" target="_blank" rel="noopener">'
        f'查看开源管线 ↗</a></div>'
        f'</div>{spec}</header>')

    controls = (
        '<nav class="controls" aria-label="页面导航与数据筛选">'
        '<div class="jump-nav"><a href="#lowest">最低价</a><a href="#quick">价格速览</a>'
        '<a href="#prices">完整价格</a>'
        '<a href="#changes">变动流水</a><a href="#news">官方公告</a></div>'
        '<div class="control-groups"><span class="control-label">FILTER</span>'
        '<div class="seg" role="group" aria-label="地区筛选">'
        '<button data-region-btn="all" class="on" aria-pressed="true">全部</button>'
        '<button data-region-btn="intl" aria-pressed="false">国际</button>'
        '<button data-region-btn="domestic" aria-pressed="false">国内</button></div>'
        '<div class="seg" role="group" aria-label="币种显示">'
        '<button data-cur-btn="cny" class="on" aria-pressed="true">折算 ¥</button>'
        '<button data-cur-btn="orig" aria-pressed="false">原币</button></div>'
        '</div></nav>')

    lowest = _cheapest_chart(providers_cfg, recs, rate)
    quick = _quick_chart(providers_cfg, recs, rate)

    # ---- 价格区
    prov_html = "".join(_prov_section(cfg, recs.get(cfg["id"]), rate)
                        for cfg in providers_cfg)
    prices = (
        f'<section class="block" id="prices">'
        f'<div class="section-head"><div><p class="sec-eyebrow">02 / PRICES</p>'
        f'<h2 class="sec-title">完整价格账本</h2></div>'
        f'<p class="sec-sub">按厂商分组 · 单位：每百万 tokens<br>'
        f'<span class="mono">USD 按 1 USD ≈ ¥{_fmt(rate)} 折算（标注 ≈）</span></p></div>'
        f'{prov_html}</section>')

    # ---- 变动流水
    if changes:
        items = "".join(_chg_item(ch) for ch in reversed(changes[-120:]))
        chg_html = f'<ul class="chg-list">{items}</ul>'
    else:
        chg_html = ('<div class="blank">暂无变动记录 —— 管线每小时对比官网价格页, '
                    '一旦有价格或活动变化, 会自动出现在这里。</div>')
    changes_sec = (
        f'<section class="block" id="changes">'
        f'<div class="section-head"><div><p class="sec-eyebrow">03 / CHANGES</p>'
        f'<h2 class="sec-title">价格变动流水</h2></div>'
        f'<p class="sec-sub">官网价格页变化时自动留痕，新记录在前<br>'
        f'红 = 涨价 / 新增，绿 = 降价</p></div>{chg_html}</section>')

    # ---- 公告区
    news_cards = "".join(_news_card(cfg, news_recs.get(cfg["id"], {}))
                         for cfg in providers_cfg if cfg.get("news_url"))
    news_sec = (
        f'<section class="block" id="news">'
        f'<div class="section-head"><div><p class="sec-eyebrow">04 / NOTICES</p>'
        f'<h2 class="sec-title">官方公告雷达</h2></div>'
        f'<p class="sec-sub">来自各厂商官网公告 / changelog 页面<br>每小时抓取新条目</p></div>'
        f'<div class="news-grid">{news_cards}</div></section>')

    footer = (
        f'<footer><div>'
        f'<p style="margin:0 0 6px">数据由各厂商公开官网页面自动抓取, 经 Claude '
        f'结构化抽取生成, 每小时运行一次; 仅供比价参考, 实际价格与活动以'
        f'各厂商官网为准。开源管线: '
        f'<a href="{REPO_URL}" target="_blank" rel="noopener">{REPO_URL}</a></p>'
        f'<p style="margin:0" class="mono">generated {gen} UTC+8 · '
        f'pipeline {REPO_URL}</p></div>'
        f'<div class="mono" style="align-self:flex-end">红涨绿跌 · seed 数据待校准'
        f'</div></footer>')

    page = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<link rel="icon" href="data:,">'
        '<title>大模型 API 价格看板 · LLM Price Watch</title>'
        '<meta name="description" content="自动抓取 OpenAI / Anthropic / Google / '
        'DeepSeek / Qwen / 豆包 / 智谱 / Kimi 等官网价格页, 每小时更新的大模型 '
        'API 价格对比、变动流水与官方公告。">'
        f'<style>{CSS}</style></head>'
        f'<body data-region="all" data-currency="cny">'
        f'<a class="skip-link" href="#main-content">跳到主要内容</a>{ticker}'
        f'<div class="wrap">{masthead}{controls}'
        f'<main id="main-content">{lowest}{quick}{prices}{changes_sec}{news_sec}</main>{footer}</div>'
        f'<script>{JS}</script></body></html>')

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DIR / "index.html").write_text(page, encoding="utf-8")

    # 原始数据一并发布, 方便他人复用
    (SITE_DIR / "data.json").write_text(json.dumps({
        "generated_at": meta.get("generated_at"),
        "fx": fx,
        "providers": {cfg["id"]: {
            "name": cfg.get("name_cn") or cfg["name"],
            "region": cfg.get("region"),
            "pricing_url": cfg.get("pricing_url") or (cfg.get("pricing_urls") or [None])[0],
            "news_url": cfg.get("news_url"),
            "record": recs.get(cfg["id"]),
        } for cfg in providers_cfg},
        "changes": changes,
        "news": {cfg["id"]: news_recs.get(cfg["id"], {}).get("entries", [])
                 for cfg in providers_cfg},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    return SITE_DIR / "index.html"
