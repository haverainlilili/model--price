# 大模型 API 价格看板 · LLM Price Watch

自动抓取各大模型厂商**官网公开页面**，每小时更新一次，生成 API 价格、官网套餐与额度、联网搜索、AI 生图、AI 生视频、价格变动流水及官方公告追踪的静态网站。生图/视频模块只展示客观能力、官方样例和严格同口径价格，不做主观效果评分。

**线上地址**: https://model-price.minggemini3test1.online/

## 覆盖厂商

| 国际 | 国内 |
|---|---|
| Anthropic (Claude)、OpenAI、Google (Gemini)、xAI (Grok)、Mistral | DeepSeek、阿里云百炼 (Qwen)、火山方舟 (豆包)、智谱 (GLM)、月之暗面 (Kimi)、MiniMax |

### 联网搜索覆盖（25 家）

- **模型内置搜索（11）**：Anthropic、OpenAI、Google Gemini、xAI、Mistral、DeepSeek、Qwen、豆包、GLM、Kimi、MiniMax。
- **AI / RAG 搜索 API（7）**：Tavily、Exa、You.com、Linkup、Jina AI Search、Firecrawl Search、博查 AI Search。
- **SERP / 搜索引擎结果 API（7）**：Brave Search API、Perplexity Search API、Serper、SerpApi、Google Custom Search JSON API、DataForSEO、Bright Data。

只在官网能直接或机械换算成 USD/千次请求时进入价格柱状图；token、搜索深度、额外结果、正文抓取和企业询价不会混入柱高。Google Custom Search JSON API 仅对存量客户开放，并将在 2027-01-01 停服，页面会明确标注。

### AI 生图覆盖（16 家）

OpenAI、Google Gemini Image、xAI Grok Imagine、Black Forest Labs、Stability AI、Adobe Firefly、Ideogram、Recraft、Leonardo、Midjourney、Seedream、阿里云 Wan、智谱 CogView、腾讯混元、MiniMax 与可灵。Midjourney 的官方“无公开 API”状态也会明确展示，不使用第三方包装价。

单张价格柱图只纳入**原币种、完整生成费、同 standard/premium 档和约 1MP 正方形输出**；输入另收费、token/像素动态价、“from”起价、2K/4K 专属价、订阅折算与企业询价只进事实表。样例优先显示稳定的厂商自有媒体，否则显示第一方样例/产品页入口；厂商平台托管的社区作品会明确标注为社区来源。

### AI 生视频覆盖（18 家）

OpenAI Sora、Google Veo、xAI Grok Imagine、Runway、Adobe Firefly、Luma、可灵、MiniMax/Hailuo、Seedance、阿里云 Wan、Vidu、PixVerse、Pika、腾讯混元、Stability、LTX、Haiper 与 AWS Nova Reel。

每秒价格柱图严格按**原币种 × 480p/720p/1080p × 原生音频/无音频**三项基础口径分组，组间柱高不可比较；同组内仍需核对柱下帧率、时长、队列、模式与促销口径。动态 token×像素价、强制月费边际价、订阅折算和 custom/enterprise 价格不进入柱图。已公告停用日期的 Sora 2、Nova Reel 使用斜纹标记；Stability 托管 SVD API 的停用状态也保留在事实表。

## 工作原理

```
providers.yaml + websearch.yaml + imagegen.yaml + videogen.yaml 配置官网入口
        │
        ▼
生产服务器 systemd timer 每小时触发
        │
        ▼
抓取官网页面 ── 页面 hash 无变化? ──是──> 跳过抽取 (零 API 成本)
        │ 否
        ▼
OpenAI 兼容接口结构化抽取 (JSON Schema + Pydantic 校验)
  · 价格页 → 模型 / 输入价 / 输出价 / 缓存价 / 币种 / 活动备注
  · 套餐页 → 套餐价格 / 官网明示额度 / 刷新窗口 / 支持模型
  · 联网搜索页 → 计费方式 / 引用来源 / 是否默认开启 / 客观限制
  · 生图页 → API 状态 / 单张价 / 分辨率 / 编辑与参考图 / 输出格式
  · 生视频页 → API 状态 / 每秒价 / 分辨率 / 时长 / 帧率 / 原生音频
  · 公告页 → 日期 / 标题 / 链接 / 摘要
        │
        ▼
与上次数据 diff → 价格变动自动记入流水 (红涨绿跌)
        │
        ▼
重新生成静态站点 (零依赖单 HTML) → Caddy 立即提供访问
        │
        └── 提交 data/ + site/ → GitHub Pages 镜像
```

要点:

- **页面无变化不调 API**: 内容 hash 相同就直接跳过，绝大多数小时级运行是零成本的；只有页面真的变了才花一次抽取费用。
- **套餐额度只保留官网口径**: 不导入社区实测值，不把 prompt 换算成请求，不由周额度推算月额度；官网使用「约」「最多」或相对倍数时保留原文。
- **五个一级模块独立切换**: 顶部可在 API 价格、套餐与额度、联网搜索、AI 生图、AI 生视频之间切换；URL hash 可直接定位模块。
- **媒体价格严格分组**: 生图不混分辨率、质量与币种；生视频不混分辨率、原生音频与币种。官方页面未提供机械可比价格时只显示原始计费口径。
- **第一方样例而非本站伪造输出**: 只嵌入或链接厂商第一方发布/托管的图片与样片，并显示原始来源；社区作品明确标注，不制作主观画质排名。
- **联网搜索只比客观事实**: 自动抓取官网公开说明中的计费方式、是否返回引用来源、是否默认开启等字段；官网没明确说明的显示「—」，不做主观效果打分。
- **单厂商失败不影响整体**: 任何一家抓取/解析失败都保留旧数据并在站点上标注，下一小时自动重试。
- **活动追踪两条线**: 价格页快照 diff(最可靠)+ 官网公告/changelog 新条目。登录后才能看的站内信、邮件/公众号推送不在覆盖范围。
- **币种换算**: 站点支持「原币 / 折算人民币」切换，汇率每小时从 frankfurter.dev 更新(失败自动回退缓存与备用源)。

## 套餐测评参考与口径区别

如果你需要更具体的套餐横向测评，可以参考 [wmpeng/codingplan](https://github.com/wmpeng/codingplan)。该项目覆盖更多 Coding Plan / Token Plan 渠道，并提供平台评价、实测或估算用量、性价比分析与选择建议。

本项目采用不同的数据口径：**只收录厂商官网直接标注的价格、额度和刷新周期**，不导入社区实测值，不把 prompt、积分或周额度自行换算成请求数、Token 或月用量。两者适合搭配阅读，但测评项目中的估算结果不会作为本项目的数据源。

## 目录结构

```
providers.yaml        # API 价格 / 套餐 / 公告厂商配置
websearch.yaml        # 联网搜索独立目录（模型工具 / AI Search / SERP）
imagegen.yaml         # 生图 API、规格与官方样例来源
videogen.yaml         # 生视频 API、规格与官方样例来源
scraper/
  fetch.py            # 抓取层: requests + 无头浏览器(渲染 JS 页面)
  models.py           # Pydantic 抽取 schema (价格 / 套餐 / 搜索 / 生图 / 生视频 / 公告)
  extract.py          # OpenAI 结构化抽取 (openai SDK)
  history.py          # 数据持久化 + 价格 diff
  fx.py               # 汇率
  run.py              # 主流程
  build_site.py       # 静态站点生成
data/                 # 价格 / 套餐 / 搜索 / imagegen / videogen / 变动 / 公告
site/                 # 生成的站点 (发布到 Pages)
scripts/make_seed.py       # API 价格一次性种子数据
scripts/make_websearch.py  # 联网搜索客观事实种子数据
scripts/make_generation.py # 生图/生视频第一方事实种子（不会覆盖成功自动抽取）
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
export MEDIA_EXTRACT_BUDGET=5          # 每种媒体每小时最多调用 5 次抽取
.venv/bin/python scripts/make_generation.py  # 首次初始化官方事实种子
.venv/bin/python -m scraper             # 抓取 + 抽取 + 建站
.venv/bin/python -m scraper --build-only  # 只重建站点
.venv/bin/python -m scraper --only zhipu  # 只处理单个厂商(调试)
```

抽取模型默认 `gpt-5.6-sol`，可用环境变量 `OPENAI_MODEL` 覆盖（如 `gpt-5.6-terra` / `gpt-5.6-luna` 降低成本）；兼容接口地址通过 `OPENAI_BASE_URL` 配置。

## 自动发布抓取结果

生产服务器（`176.122.165.19`，目录 `/opt/model-price`）由 `model-price.timer` 每小时触发抓取并重建站点，Caddy 从 `/opt/model-price/site` 提供线上访问。抓取结束后运行 `scripts/publish_updates.sh`：脚本先执行测试，随后只提交 `data/` 与 `site/` 到 `main`；没有文件变化时不会生成空提交，`.env`、部署密钥和缓存不会进入 Git。

GitHub Actions 不再重复抓取，只在 `main` 的 `site/` 更新后发布 GitHub Pages。这样从 GitHub 默认分支下载项目时，包含最近一次成功抓取的数据和对应界面。

生产服务器需要优先保障其他网络服务时，可使用 `deploy/systemd/` 下的独立网络命名空间配置。它只限制 `model-price.service`：默认下载 2 Mbit/s、上传 256 Kbit/s，不修改主网卡上的其他服务。部署前请确认隔离网段 `10.203.0.0/30` 未被占用。需要调整时，在 `model-price-network-limit.service` 的 systemd override 中设置 `MODEL_PRICE_DOWNLOAD_RATE` 和 `MODEL_PRICE_UPLOAD_RATE`；这两个变量不属于项目 `.env`。

## 数据说明

- 站点上的价格均来自各厂商官网公开页面，**仅供比价参考**；限时折扣、档位计价等细节以官网为准。
- 首次成功抽取前，已人工核对官网的联网搜索、生图和生视频记录显示「官网事实种子 · 待自动校准」；媒体种子的事实、来源与样例由显式 SHA-256 审核清单约束，只记录 `seeded_at`/`verified_at`，真实抓取前 `fetched_at` 为 null。空缺字段不会猜测。
- 生图/生视频各自按 `MEDIA_EXTRACT_BUDGET` 分批自动校准；主来源失败或任一次要来源缺失时保留已有完整事实，覆盖减少、生命周期/EOL 变化须连续两轮语义一致后才接受。
- 顶部“国际价区 / 中国价区”按服务端点或官方价格区域筛选，而不是按厂商注册地筛选；没有区域/币种证据的条目同时保留在两边。
- CLI 自身用 `data/.scraper.lock` 串行化所有抓取/构建入口；通用 `scripts/deploy.sh` 在拉代码、装依赖和构建前同时取得部署锁，cron 另有整轮 `flock` 与 50 分钟 `timeout`。生产 systemd timer 也不会并发启动同一 oneshot 服务。
- 若通用部署目录位于权限为 0700/0750 的 home 下且 Caddy 已安装，脚本会只给 Caddy 完整父目录链 traverse 和 `site/` 读取 ACL，并复验可读性；`.env` 始终保持 `0600`。若先运行部署脚本、后安装 Caddy，需按终端提示再运行一次部署脚本。
- 原始数据随站点发布在 `site/data.json`，可直接复用。

## License

MIT
