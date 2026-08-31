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
  --bg:#F2F4F7; --panel:#FFFFFF; --ink:#1B2735; --ink2:#5C6878;
  --line:#DCE1E8; --line2:#C7CEDB;
  --up:#C13B2A; --up-bg:#FBEFED;
  --down:#17754E; --down-bg:#EAF5EF;
  --new:#20508F; --new-bg:#EBF1F9;
  --seed:#8A6A15; --seed-bg:#FBF3D8;
  --err:#A32F21; --err-bg:#F9ECEA;
  --mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,"Liberation Mono",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 var(--sans)}
a{color:var(--new);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--new);outline-offset:2px;border-radius:2px}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px}
.up{color:var(--up)} .down{color:var(--down)} .neu{color:var(--ink2)}
.strike{text-decoration:line-through;color:var(--ink2)}

/* ---- 顶部行情条(签名元素) ---- */
.ticker{display:flex;align-items:stretch;background:#141D28;border-bottom:3px solid var(--ink)}
.ticker-label{flex:none;display:flex;align-items:center;padding:0 16px;
  font:600 11px/1 var(--mono);letter-spacing:.22em;color:#8FA1B8;
  border-right:1px solid #2A3644}
.ticker-view{flex:1;overflow:hidden}
.ticker-track{display:inline-flex;align-items:center;white-space:nowrap;
  padding:10px 0;animation:tk 60s linear infinite;will-change:transform}
.ticker:hover .ticker-track{animation-play-state:paused}
@keyframes tk{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.chip{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-size:12.5px;color:#C7D2DF;margin-right:38px}
.chip b{color:#fff;font-weight:600}
.chip .up{color:#FF927D} .chip .down{color:#5BCB9B} .chip .neu{color:#93A5BB}

/* ---- 报头 ---- */
.masthead{display:flex;justify-content:space-between;gap:24px;align-items:flex-end;
  padding:34px 0 20px;border-bottom:2px solid var(--ink);margin-bottom:6px;flex-wrap:wrap}
.eyebrow{font:600 11px/1 var(--mono);letter-spacing:.24em;color:var(--ink2);
  text-transform:uppercase;margin-bottom:10px}
h1{margin:0;font-size:clamp(26px,4vw,38px);line-height:1.15;letter-spacing:.01em}
.sub{margin:8px 0 0;color:var(--ink2);font-size:13.5px}
.spec{font-family:var(--mono);font-variant-numeric:tabular-nums;font-size:12px;
  color:var(--ink2);border:1px solid var(--line2);border-radius:8px;
  padding:10px 14px;background:var(--panel);min-width:250px}
.spec div{display:flex;justify-content:space-between;gap:18px;padding:2px 0}
.spec b{color:var(--ink);font-weight:600}

/* ---- 价格速览图(首页顶部, 纯 CSS 横向条形) ---- */
.quick{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px 10px;margin-bottom:28px}
.quick-head{display:flex;align-items:baseline;gap:18px;flex-wrap:wrap;
  margin:0 0 4px}
.quick-title{font-size:15px;font-weight:600;margin:0}
.blegend{display:inline-flex;gap:14px;align-items:center;
  font-size:12px;color:var(--ink2)}
.sw{display:inline-block;width:14px;height:9px;border-radius:0 3px 3px 0;
  margin-right:6px;vertical-align:-1px}
.sw-in{background:#2a78d6}.sw-out{background:#eb6834}
.bnote{margin-left:auto;font-size:11.5px}
.bticks{display:grid;grid-template-columns:minmax(116px,168px) 1fr;gap:0 12px;
  padding:2px 0 4px}
.tarea{position:relative;height:16px;border-bottom:1px solid var(--line2);
  margin-right:86px}
.tarea i{position:absolute;bottom:2px;transform:translateX(-50%);
  font:500 10.5px var(--mono);font-style:normal;color:var(--ink2);
  font-variant-numeric:tabular-nums}
.bgroup+.bgroup{border-top:1px solid var(--line2);margin-top:6px;padding-top:4px}
.brow{display:grid;grid-template-columns:minmax(116px,168px) 1fr;gap:0 12px;
  align-items:center;padding:3px 0}
.bprov{grid-column:1/-1;font-weight:600;font-size:13px;padding:3px 0 2px}
.btag{font:600 10px var(--mono);color:var(--ink2);margin-left:8px;
  letter-spacing:.08em}
.bmodel{font-family:var(--mono);font-size:12px;color:var(--ink);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bbars{display:flex;flex-direction:column;margin-right:86px}
.bbar{position:relative;height:11px;border-radius:0 4px 4px 0}
.b-in{background:#2a78d6;margin-bottom:2px}
.b-out{background:#eb6834}
.b-none{background:none}
.bbar i{position:absolute;left:100%;top:50%;transform:translateY(-52%);
  padding-left:6px;font:500 11px/1 var(--mono);font-style:normal;
  font-variant-numeric:tabular-nums;color:var(--ink);white-space:nowrap}
.bfoot{margin:10px 0 2px;font-size:11.5px;color:var(--ink2)}

/* ---- 吸顶控制条 ---- */
.controls{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;
  gap:14px;background:rgba(242,244,247,.93);backdrop-filter:blur(6px);
  padding:10px 0;margin-bottom:28px;flex-wrap:wrap}
.seg{display:inline-flex;background:var(--panel);border:1px solid var(--line2);
  border-radius:999px;padding:3px;gap:2px}
.seg button{border:0;background:transparent;font:600 12.5px/1 var(--sans);
  color:var(--ink2);padding:7px 14px;border-radius:999px;cursor:pointer}
.seg button.on{background:var(--ink);color:#fff}

/* ---- 区块标题 ---- */
section.block{margin:46px 0}
.sec-eyebrow{font:600 11px var(--mono);letter-spacing:.22em;color:var(--ink2);
  text-transform:uppercase;margin:0 0 3px}
h2.sec-title{margin:0 0 5px;font-size:20px}
.sec-sub{margin:0 0 18px;color:var(--ink2);font-size:13px}

/* ---- 厂商价格块 ---- */
.prov{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  margin:0 0 18px;overflow:hidden}
.prov-head{display:flex;justify-content:space-between;align-items:baseline;
  gap:14px;padding:13px 18px;border-bottom:1px solid var(--line);flex-wrap:wrap;
  background:linear-gradient(#FBFCFD,#F6F8FA)}
.prov-title{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.prov-title h3{margin:0;font-size:16.5px}
.tag{font:600 10.5px var(--mono);letter-spacing:.08em;padding:3px 8px;
  border-radius:4px}
.tag-region{background:#EDF0F5;color:var(--ink2);border:1px solid var(--line2)}
.badge{font-size:11px;padding:3px 9px;border-radius:999px;font-weight:600}
.b-seed{background:var(--seed-bg);color:var(--seed)}
.b-err{background:var(--err-bg);color:var(--err)}
.b-warn{background:#F0F2F5;color:var(--ink2)}
.prov-meta{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-size:11.5px;color:var(--ink2);display:flex;gap:16px;flex-wrap:wrap}
.promo{margin:0;padding:9px 18px;background:var(--up-bg);color:#7C2E1F;
  font-size:13px;border-bottom:1px solid var(--line)}
.promo b{font:600 10.5px var(--mono);letter-spacing:.18em;color:var(--up);
  margin-right:8px}

/* ---- 价格表 ---- */
.table-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:660px}
thead th{font:600 10.5px var(--mono);letter-spacing:.12em;text-transform:uppercase;
  color:var(--ink2);text-align:right;padding:10px 14px;
  border-bottom:2px solid var(--ink);background:var(--panel)}
thead th:first-child{text-align:left}
thead th.c-note-h{text-align:left}
tbody td{padding:8.5px 14px;border-bottom:1px solid var(--line);
  text-align:right;vertical-align:top}
tbody tr:last-child td{border-bottom:0}
tbody tr:nth-child(even){background:#FAFBFC}
td.c-model{font-family:var(--mono);font-size:12.5px;text-align:left;
  color:var(--ink);word-break:break-all}
td.c-note{text-align:left;font-size:12.5px;color:var(--ink2);max-width:340px}
.price{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-weight:500;white-space:nowrap}
.price-zero{color:var(--down);font-weight:700}
.empty-row td{text-align:left;padding:16px 18px;color:var(--ink2);font-size:13px}
body[data-currency=orig] .p-cny{display:none}
body[data-currency=cny] .p-orig{display:none}

/* ---- 变动流水 ---- */
.chg-list{list-style:none;margin:0;padding:0}
.chg{display:grid;grid-template-columns:104px 128px 1fr;gap:4px 16px;
  align-items:baseline;background:var(--panel);border:1px solid var(--line);
  border-left:3px solid var(--line2);border-radius:8px;padding:10px 16px;
  margin-bottom:8px}
.chg-up{border-left-color:var(--up)}
.chg-down{border-left-color:var(--down)}
.chg-new{border-left-color:var(--new)}
.chg-removed{border-left-color:var(--ink2)}
.chg-time{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-size:11.5px;color:var(--ink2)}
.chg-prov{font-weight:600;font-size:13.5px}
.chg-body{font-size:13.5px}
.chg-body .m{font-family:var(--mono);font-size:12.5px}
.arrow{font-weight:700}
.blank{background:var(--panel);border:1px dashed var(--line2);border-radius:10px;
  padding:22px;color:var(--ink2);font-size:13.5px;text-align:center}

/* ---- 公告 ---- */
.news-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));
  gap:16px}
.news-card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:14px 16px}
.news-card h3{margin:0 0 8px;font-size:15px;display:flex;
  justify-content:space-between;align-items:baseline;gap:10px}
.news-card h3 a{font:600 11px var(--mono);color:var(--ink2);letter-spacing:.08em;
  white-space:nowrap}
.news-card ul{list-style:none;margin:0;padding:0}
.n-item{padding:8px 0;border-top:1px solid var(--line)}
.n-date{display:inline-block;font:600 11px var(--mono);color:var(--ink2);
  background:#EDF0F5;border-radius:4px;padding:2px 6px;margin-right:8px}
.n-title{font-weight:600;font-size:13.5px}
.n-summary{margin:4px 0 0;font-size:12.5px;color:var(--ink2)}
.news-empty{color:var(--ink2);font-size:12.5px;padding:8px 0 2px}

/* ---- 页脚 ---- */
footer{margin:64px 0 42px;padding-top:18px;border-top:2px solid var(--ink);
  color:var(--ink2);font-size:12.5px;display:flex;justify-content:space-between;
  gap:20px;flex-wrap:wrap}
footer .mono{font-size:11.5px}

/* ---- 筛选 ---- */
body[data-region=intl] .prov[data-region=domestic],
body[data-region=intl] .news-card[data-region=domestic],
body[data-region=intl] .bgroup[data-region=domestic]{display:none}
body[data-region=domestic] .prov[data-region=intl],
body[data-region=domestic] .news-card[data-region=intl],
body[data-region=domestic] .bgroup[data-region=intl]{display:none}

@media(max-width:820px){
  .masthead{align-items:flex-start;flex-direction:column;padding-top:26px}
  .spec{min-width:0;width:100%}
  .chg{grid-template-columns:1fr;gap:2px 0}
  .chg-time::after{content:" · "}
  .ticker-label{display:none}
  .quick{padding:12px 12px 6px}
  .brow{grid-template-columns:minmax(96px,132px) 1fr;gap:0 8px}
  .bbars,.tarea{margin-right:62px}
  .bnote{margin-left:0;flex-basis:100%}
}
@media(prefers-reduced-motion:reduce){
  .ticker-track{animation:none}
  .ticker-view{overflow-x:auto}
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
  try{var c=localStorage.getItem('lpw-cur');if(c==='orig'||c==='cny')setCur(c)}catch(e){}
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
            f'<div class="ticker-view"><div class="ticker-track">{row}{row}'
            f'</div></div></div>')


def _quick_chart(providers_cfg: list, recs: dict, rate: float) -> str:
    """首页顶部价格速览: 每家厂商价格页最前的 2 个模型, 输入/输出双条,
    对数刻度横向条形图(纯 CSS, 价格跨 2-3 个数量级, 线性刻度会把便宜模型压扁)。

    跨厂商可比的前提是同一币种: USD 统一按汇率折算成人民币。
    「最新 2 个」按各官网价格页的排列顺序取(各家都把最新模型放在最前)。
    """
    groups, vals = [], []
    for cfg in providers_cfg:
        rec = recs.get(cfg["id"]) or {}
        rows = []
        for m in (rec.get("models") or [])[:2]:
            cur = (m.get("currency") or rec.get("currency") or "").upper()
            fx = rate if cur == "USD" else 1.0

            def cv(v, _fx=fx):
                return float(v) * _fx if isinstance(v, (int, float)) and v > 0 else None

            ci, co = cv(m.get("input_per_1m")), cv(m.get("output_per_1m"))
            if ci is None and co is None:
                continue
            rows.append((m.get("model", ""), ci, co))
            vals += [v for v in (ci, co) if v]
        if rows:
            groups.append((cfg, rows))
    if not groups or not vals:
        return ""

    lo, hi = min(vals), max(vals)
    span = math.log10(hi) - math.log10(lo) if hi > lo else 1.0

    def pct(v) -> str:
        if v is None:
            return "0"
        return f"{max(3.0, min(100.0, (math.log10(v) - math.log10(lo)) / span * 100)):.1f}"

    def vlabel(v) -> str:
        return f"¥{_fmt(round(v, 2))}" if v is not None else "—"

    # 对数刻度: 取数据范围内的 10 的幂做刻度(¥1 / ¥10 / ¥100 / ...)
    ticks = []
    e = math.floor(math.log10(lo))
    while 10 ** e <= hi * 1.001 and len(ticks) < 5:
        if 10 ** e >= lo * 0.999:
            ticks.append(10 ** e)
        e += 1
    ticks_html = "".join(f'<i style="left:{pct(t)}%">¥{_fmt(t)}</i>' for t in ticks)

    parts = []
    for cfg, rows in groups:
        region = cfg.get("region", "")
        dr = "domestic" if region == "国内" else "intl"
        inner = []
        for model, ci, co in rows:
            bars = (f'<div class="bbar b-in" style="width:{pct(ci)}%">'
                    f'<i>{vlabel(ci)}</i></div>'
                    f'<div class="bbar b-out" style="width:{pct(co)}%">'
                    f'<i>{vlabel(co)}</i></div>')
            inner.append(f'<div class="brow"><span class="bmodel">{_e(model)}</span>'
                         f'<div class="bbars">{bars}</div></div>')
        parts.append(f'<div class="bgroup" data-region="{dr}">'
                     f'<div class="bprov">{_e(cfg.get("name_cn") or cfg["name"])}'
                     f'<span class="btag">{_e(region)}</span></div>'
                     f'{"".join(inner)}</div>')

    return (
        '<section class="quick" id="quick" aria-label="价格速览图">'
        '<div class="quick-head"><h2 class="quick-title">价格速览 · 每家最新的 2 个模型'
        '</h2><div class="blegend"><span><i class="sw sw-in"></i>输入</span>'
        '<span><i class="sw sw-out"></i>输出</span>'
        '<span class="bnote">统一折算人民币 · 对数刻度</span></div></div>'
        f'<div class="bticks"><div></div><div class="tarea">{ticks_html}</div></div>'
        f'{"".join(parts)}'
        f'<p class="bfoot">条长为对数刻度(图中价格跨约 {round(span)} 个数量级, 线性刻度'
        f'会把低价模型压成一条线) · USD 按 1 USD ≈ ¥{_fmt(rate)} 折算 · 每家取官网'
        '价格页最前的 2 个模型(即最新), 完整价格与备注见下方明细表。</p></section>')


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
    meta_bits = [x for x in (count, updated, link) if x]
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

    return (f'<section class="prov" data-region="{data_region}" id="prov-{_e(pid)}">'
            f'<div class="prov-head"><div class="prov-title"><h3>{_e(name)}</h3>'
            f'<span class="tag tag-region">{_e(region)}</span>{badge_html}</div>'
            f'<div class="prov-meta">{"".join(m and f"<span>{m}</span>" or "" for m in meta_bits)}</div></div>'
            f'{promo}<div class="table-wrap"><table>'
            f'<thead><tr><th>模型</th><th>输入 / 百万tokens</th>'
            f'<th>输出 / 百万tokens</th><th>缓存输入</th>'
            f'<th class="c-note-h">备注</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


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
            '<p class="news-empty">尚未抓到公告条目, 每小时自动重试。</p>')
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

    spec = (f'<aside class="spec">'
            f'<div><span>最近更新</span><b>{gen}</b></div>'
            f'<div><span>汇率</span><b>{_e(fx_line)}</b></div>'
            f'<div><span>汇率来源</span><b>{_e(fx_src)}{fx_stale}</b></div>'
            f'<div><span>覆盖</span><b>{len(providers_cfg)} 厂商 · {total_models} 模型</b></div>'
            f'<div><span>变动记录</span><b>{len(changes)} 条</b></div>'
            f'</aside>')

    masthead = (
        f'<header class="masthead"><div>'
        f'<p class="eyebrow">LLM PRICE WATCH · HOURLY</p>'
        f'<h1>大模型 API 价格看板</h1>'
        f'<p class="sub">自动抓取各厂商官网价格页 · 每小时更新 · 红涨绿跌 · '
        f'时间均为北京时间 · 实际价格以各厂商官网为准</p>'
        f'</div>{spec}</header>')

    controls = (
        '<div class="controls">'
        '<div class="seg" role="group" aria-label="地区筛选">'
        '<button data-region-btn="all" class="on" aria-pressed="true">全部</button>'
        '<button data-region-btn="intl" aria-pressed="false">国际</button>'
        '<button data-region-btn="domestic" aria-pressed="false">国内</button></div>'
        '<div class="seg" role="group" aria-label="币种显示">'
        '<button data-cur-btn="cny" class="on" aria-pressed="true">折算 ¥</button>'
        '<button data-cur-btn="orig" aria-pressed="false">原币</button></div>'
        '</div>')

    quick = _quick_chart(providers_cfg, recs, rate)

    # ---- 价格区
    prov_html = "".join(_prov_section(cfg, recs.get(cfg["id"]), rate)
                        for cfg in providers_cfg)
    prices = (
        f'<section class="block" id="prices">'
        f'<p class="sec-eyebrow">PRICES</p>'
        f'<h2 class="sec-title">价格对比</h2>'
        f'<p class="sec-sub">按厂商分组 · 单位: 每百万 tokens · '
        f'<span class="mono">USD 按 1 USD ≈ ¥{_fmt(rate)} 折算(标注 ≈)</span></p>'
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
        f'<p class="sec-eyebrow">CHANGES</p>'
        f'<h2 class="sec-title">价格变动流水</h2>'
        f'<p class="sec-sub">每次官网价格页内容变化时自动记录, 新进在前 · '
        f'红 = 涨价 / 新增, 绿 = 降价</p>{chg_html}</section>')

    # ---- 公告区
    news_cards = "".join(_news_card(cfg, news_recs.get(cfg["id"], {}))
                         for cfg in providers_cfg if cfg.get("news_url"))
    news_sec = (
        f'<section class="block" id="news">'
        f'<p class="sec-eyebrow">NOTICES</p>'
        f'<h2 class="sec-title">官方公告</h2>'
        f'<p class="sec-sub">来自各厂商官网公告 / changelog 页, 每小时抓取新条目</p>'
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
        '<title>大模型 API 价格看板 · LLM Price Watch</title>'
        '<meta name="description" content="自动抓取 OpenAI / Anthropic / Google / '
        'DeepSeek / Qwen / 豆包 / 智谱 / Kimi 等官网价格页, 每小时更新的大模型 '
        'API 价格对比、变动流水与官方公告。">'
        f'<style>{CSS}</style></head>'
        f'<body data-region="all" data-currency="cny">{ticker}'
        f'<div class="wrap">{masthead}{controls}{quick}'
        f'<main>{prices}{changes_sec}{news_sec}</main>{footer}</div>'
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
