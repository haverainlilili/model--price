# 大模型 API 价格看板 · LLM Price Watch

自动抓取各大模型厂商**官网公开价格页**，每小时更新一次，生成价格对比、价格变动流水与官方公告追踪的静态网站。

**线上地址**: https://model-price.minggemini3test1.online/

## 覆盖厂商

| 国际 | 国内 |
|---|---|
| Anthropic (Claude)、OpenAI、Google (Gemini)、xAI (Grok)、Mistral | DeepSeek、阿里云百炼 (Qwen)、火山方舟 (豆包)、智谱 (GLM)、月之暗面 (Kimi)、MiniMax |

## 工作原理

```
providers.yaml 配置各厂商入口
        │
        ▼
每小时 GitHub Actions 触发 (cron: 23 * * * *)
        │
        ▼
抓取官网页面 ── 页面 hash 无变化? ──是──> 跳过抽取 (零 API 成本)
        │ 否
        ▼
Claude 结构化抽取 (官方 SDK messages.parse + Pydantic schema)
  · 价格页 → 模型 / 输入价 / 输出价 / 缓存价 / 币种 / 活动备注
  · 公告页 → 日期 / 标题 / 链接 / 摘要
        │
        ▼
与上次数据 diff → 价格变动自动记入流水 (红涨绿跌)
        │
        ▼
重新生成静态站点 (零依赖单 HTML) → 提交 data/ → 发布 GitHub Pages
```

要点:

- **页面无变化不调 API**: 内容 hash 相同就直接跳过，绝大多数小时级运行是零成本的；只有页面真的变了才花一次抽取费用。
- **单厂商失败不影响整体**: 任何一家抓取/解析失败都保留旧数据并在站点上标注，下一小时自动重试。
- **活动追踪两条线**: 价格页快照 diff(最可靠)+ 官网公告/changelog 新条目。登录后才能看的站内信、邮件/公众号推送不在覆盖范围。
- **币种换算**: 站点支持「原币 / 折算人民币」切换，汇率每小时从 frankfurter.dev 更新(失败自动回退缓存与备用源)。

## 目录结构

```
providers.yaml        # 厂商入口配置 —— 增删厂商只改这个文件
scraper/
  fetch.py            # 抓取层: requests + 无头浏览器(渲染 JS 页面)
  models.py           # Pydantic 抽取 schema (价格页 / 公告页)
  extract.py          # OpenAI 结构化抽取 (openai SDK)
  history.py          # 数据持久化 + 价格 diff
  fx.py               # 汇率
  run.py              # 主流程
  build_site.py       # 静态站点生成
data/                 # 各厂商价格记录 / 变动流水 / 公告 (机器人每小时提交)
site/                 # 生成的站点 (发布到 Pages)
scripts/make_seed.py  # 一次性种子数据(首次抽取前的初始展示)
.github/workflows/update.yml
```

## 自部署

1. Fork 或复制本仓库。
2. 在仓库 **Settings → Secrets and variables → Actions** 添加 Secret `OPENAI_API_KEY`；使用兼容接口时再添加 Secret `OPENAI_BASE_URL`，并用 Variable `OPENAI_MODEL` 指定模型。不配置 Key 也能跑，但只有种子数据、不会抽取新内容。
3. 在 **Settings → Pages** 把 Source 设为 **GitHub Actions**。
4. 手动触发一次 workflow(Actions → update → Run workflow)验证，之后每小时自动运行。

本地运行:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium   # 渲染智谱/火山方舟的 JS 页面需要
export OPENAI_API_KEY=sk-...
.venv/bin/python -m scraper             # 抓取 + 抽取 + 建站
.venv/bin/python -m scraper --build-only  # 只重建站点
.venv/bin/python -m scraper --only zhipu  # 只处理单个厂商(调试)
```

抽取模型默认 `gpt-5.6-sol`，可用环境变量 `OPENAI_MODEL` 覆盖（如 `gpt-5.6-terra` / `gpt-5.6-luna` 降低成本）；兼容接口地址通过 `OPENAI_BASE_URL` 配置。

## 数据说明

- 站点上的价格均来自各厂商官网公开页面，**仅供比价参考**；限时折扣、档位计价等细节以官网为准。
- 首次成功抽取前，部分厂商展示「种子数据 · 待校准」(2026-08-27 手工录入)，首次抓取成功后自动替换。
- 原始数据随站点发布在 `site/data.json`,可直接复用。

## License

MIT
