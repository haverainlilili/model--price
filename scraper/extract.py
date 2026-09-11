"""OpenAI 系接口结构化抽取: 官网价格页 -> PricingPage, 公告页 -> NewsPage。

实现要点(为什么这样写):
- openai SDK 自动读环境变量 OPENAI_API_KEY / OPENAI_BASE_URL, 所以
  OpenAI 官方 API、各类中转/网关、本地推理服务都只靠 .env(本地)或
  Secrets/Variables(CI)配置, 代码零改动。
- 结构化输出用 response_format={"type":"json_object"} + 提示词内嵌
  JSON Schema, 响应经 Pydantic 校验, 校验不过带错误自动重试一次。
  不用 json_schema 严格模式: 它要求所有字段必填, 与本 schema 的大量
  Optional 字段冲突, 且不少兼容端点不支持。
- 抽取模型由 OPENAI_MODEL 指定, 默认 gpt-5.6-sol。
- 需要 OPENAI_API_KEY; 没有密钥时上层(run.py)直接跳过抽取。
"""
from __future__ import annotations

import json
import os

import openai
from pydantic import BaseModel, ValidationError

from .models import (ImageGenerationPage, NewsPage, PlansPage, PricingPage,
                     VideoGenerationPage, WebSearchPage)

MODEL = os.environ.get("OPENAI_MODEL") or "gpt-5.6-sol"
MAX_PAGE_CHARS = 250_000
MAX_OUTPUT_TOKENS = 24000

# 端点不支持 response_format / max_completion_tokens 时置 True, 之后走最小参数集
_MINIMAL_PARAMS = False


class ExtractionError(RuntimeError):
    """一次抽取失败(网络/限流/校验), 调用方应保留旧数据并记录状态。"""


PRICING_SYSTEM = """你是一个严谨的大模型厂商官网价格页解析器, 把页面文本抽取成结构化数据。

规则:
1. 只抽取 API 按量计费(按 token)的价格, 单位统一为「每百万 tokens」。页面若按每千 tokens 计价, 换算成每百万。
2. currency 填该价格使用的币种: USD / CNY / EUR。
3. 输入价填 input_per_1m, 输出价填 output_per_1m, 缓存命中的输入价填 cached_input_per_1m。页面没写的字段留 null, 严禁编造或估算。
4. 限时折扣/活动价: 折后价填价格字段, 原价和活动说明写进 note, 例如「限时5折, 原价 ¥8/百万」。免费模型记 0 并在 note 注明「限时免费」。
5. 只关注 API 按量价格: 跳过订阅套餐(如 ChatGPT Plus / Claude Pro)、企业定制价、充值优惠。
6. 以对话/推理/多模态文本模型为主; embedding / rerank 等如果页面上有且价格简单, 也抽取并在 note 标注类型。
7. model 优先填写官网价格表中的实际 API 调用名/模型标识；页面只有产品名时保留产品名原文。同一模型不同上下文或峰谷价格档位拆成多行, 在 note 标注档位。
8. 官网若在同一价格列另列“模型版本”或正式产品展示名，display_name 填该原文，且每个价格档位都保留映射；否则填 null。不要把 API 调用名和版本名拼在同一字段。例如 model=deepseek-flash、display_name=DeepSeek-V4.1-Flash。
9. promotions 汇总页面上明显的促销/活动文字(整段抄录), 没有则为 null。
10. 如果页面文本不含价格表(如 JS 渲染的空壳、报错页、人机验证页), 把 page_has_pricing 设为 false 且 models 留空。"""


NEWS_SYSTEM = """你从厂商官方公告 / changelog / 新闻页文本中抽取最近的公告条目。

规则:
- date: 页面标注的日期, 保留原文格式(如 2026-08-20 / Aug 20, 2026); 没有则 null
- title: 公告标题原文
- url: 文本中明确属于该条目的链接才填, 否则 null
- summary: 不超过 60 字的一句话中文摘要
- 只抽与该厂商模型/产品/价格相关的条目, 最多 12 条, 按页面出现顺序(新的在前)
- 页面没有公告(空壳/报错页)则 entries 留空"""


WEBSEARCH_SYSTEM = """你从联网搜索产品官网的能力与价格页（web search / search API / SERP API / grounding）文本中抽取客观事实。

规则:
1. has_search: 该厂商是否提供联网/网络搜索能力, 填 true/false。
2. offerings 每项:
   - name: 搜索能力/产品名原文, 如 Search API / Grounding with Google Search / 联网搜索。
   - pricing: 官网定价原文或忠实的简短摘录；页面没写就 null。
   - price_per_1k_usd: 只在官网可直接得到「美元/1000 次请求、调用或查询」时填写。官网直接按千次报价就照录；官网按单次报价可乘 1000；官网明确 1 次基础搜索消耗固定 credit 且给出每 credit 美元价时可机械换算。token 计费、动态价、企业询价、结果数浮动或缺少映射时必须为 null。
   - price_basis: 说明上面数值对应的公开档位与口径，如「PAYG basic（1 credit/次）」/「Starter（1000次/月）」/「Standard queue」。
   - free_quota: 官网明确的免费额度原文；未说明则 null。
   - output_type: 客观描述输出形态，如「结构化搜索结果」「带引用答案」「模型内置工具」。
   - cites_sources: 响应是否包含来源 URL 或可点击引用。只有页面明确说明时才填 true/false, 没提到就 null。
   - default_on: 是否默认开启(无需手动启用或调用)。只有页面明确说明时才填 true/false, 没提到就 null。
   - note: 补充的客观说明原文(搜索深度、结果上限、额外 token/结果费用、可用区域、退役日期等), 不超过 100 字。
3. 有多个价格明显不同的搜索产品或档位时拆成多项；只有同一产品的批量折扣时保留一个基础/入门公开档，并在 pricing 或 note 中说明范围。
4. 只抽客观事实, 严禁主观打分、严禁编造。页面没提到的字段一律填 null。
5. 页面文本不含联网搜索能力说明(空壳/报错/人机验证页)时 has_search=false 且 offerings 留空。"""


IMAGEGEN_SYSTEM = """你从生图产品官网的价格页和 API 文档中抽取客观事实。

规则:
1. page_has_relevant_content：产品/价格/正式停用说明页面为 true；空白、报错、登录墙、人机验证或无关导航壳为 false。它与产品是否仍提供是两个字段。
2. product_status 填 active / discontinued / unknown；正式停用时同时填写 product_status_date 与 product_status_note。has_image_generation 表示官网是否仍提供图片生成或编辑产品；正式停用说明页应 page_has_relevant_content=true、product_status=discontinued 且本字段=false。
3. offerings 按模型或价格明显不同的公开档位拆分，每项填写：
   - name：模型/产品名原文。variant_key：同一模型按区域/质量/计费模式拆分时填写稳定简短档位键（如 global-medium / cn-premium），不可用易变说明文字；没有拆分档位可填 null。
   - api_available：官网明确提供开发者 API 才为 true；明确只有网页/应用且无官方 API 才为 false；没说则 null。
   - modes：只收录官网明确能力，使用 text-to-image / image-edit / reference / inpainting 等简短值。
   - pricing、currency：保留官网价格口径和原币种，不换汇。存在中国区/全球区/新加坡等独立价时，region 按端点填 domestic 或 intl；无独立区域含义填 null。
   - price_per_image：官网直接给出完整单张生成费，或给出固定 credits/张且公开固定 PAYG credit 单价时可机械换算。输入/提示词另收费、仅输出 token 估价、订阅额度折算、动态像素/算力、from 起价、企业询价或未固定输出数量时必须为 null，只在 pricing 里说明。
   - comparison_group：只有完整单张费、约 0.8–1.5MP 的正方形输出才可进入柱图。普通/默认质量按币种填 usd-standard-1mp 或 cny-standard-1mp；明确 high/ultra/premium 档填 usd-premium-1mp 或 cny-premium-1mp；其它一律 null。
   - comparison_width / comparison_height / quality_tier：进入柱图时必须填该价格对应的精确正方形像素宽高与 standard/premium，并与 comparison_group 一致；不进柱图全部填 null。
   - lifecycle_status / sunset_at：按官网填写 active/deprecated/sunsetting/legacy-existing-only/discontinued/self-host-only；官网未说明生命周期时填 unknown；有正式停用日必须填 YYYY-MM-DD。
   - price_basis：必须写清模型、质量、分辨率和计费档。
   - resolution、aspect_ratios、output_formats、free_quota：仅按官网原文。
   - note：限制、附加费用、订阅或 API 状态等客观说明，不超过 120 字。
4. 不把第三方托管价格当作厂商官方 API 价；不依据排行榜或体验做质量评分。
5. 有效页面明确没有/已停用生图产品时 has_image_generation=false 且 offerings 留空。无效壳页同样留空，但必须 page_has_relevant_content=false。页面没写的字段填 null/空数组，严禁猜测。"""


VIDEOGEN_SYSTEM = """你从生视频产品官网的价格页和 API 文档中抽取客观事实。

规则:
1. page_has_relevant_content：产品/价格/正式停用说明页面为 true；空白、报错、登录墙、人机验证或无关导航壳为 false。它与产品是否仍提供是两个字段。
2. product_status 填 active / discontinued / unknown；正式停用时同时填写 product_status_date 与 product_status_note。has_video_generation 表示官网是否仍提供生成式视频产品；正式停用说明页应 page_has_relevant_content=true、product_status=discontinued 且本字段=false。
3. offerings 按模型、分辨率、是否原生音频或价格明显不同的公开档位拆分，每项填写：
   - name：模型/产品名原文；官网价格表出现实际 API 模型标识时优先逐字使用该标识，不得自行改成标题式产品名。variant_key：同一模型按区域/分辨率/音频/队列/模式拆分时填写稳定简短档位键，不可用易变说明文字；没有拆分档位可填 null。
   - api_available：官网明确提供开发者 API 才为 true；明确只有网页/应用且无官方 API 才为 false；没说则 null。
   - modes：只收录官网明确能力，使用 text-to-video / image-to-video / first-last-frame / reference-to-video / video-edit 等简短值；参考视频驱动不等于视频编辑。
   - pricing、currency：保留官网原币种，不换汇。存在中国区/全球区/新加坡等独立价时，region 按端点填 domestic 或 intl；无独立区域含义填 null。
   - price_per_second：官网直接按生成秒报价；或固定 credits/秒且公开固定 PAYG credit 单价；或固定价格对应固定片段秒数时才可机械换算。官网价格页若针对固定分辨率、宽高比、时长和输入条件同时公布“元/个”和“元/秒”示例，必须使用官网原文“元/秒”数值，不得用总价重新除法得到更多小数；price_basis 与 note 必须标明固定场景及动态 token 实际结算。只有官网没有“元/秒”而明确给出固定总价和固定秒数时，才可机械换算并保留足以还原官网总价的精度。没有官网固定场景秒价的 token/像素动态计费、订阅额度折算、强制月费下的边际价、企业询价或时长不固定必须为 null。
   - comparison_price_type：price_per_second 非 null 时必须填写其事实来源：官网直接按秒计费填 direct；官网针对固定场景直接列出元/秒示例填 official-fixed-example；官网固定总价除以固定秒数填 fixed-duration-derived。其它情况为 null。
   - comparison_group：只有无需订阅折算/强制月费，且明确分辨率和音频口径才能进入柱图，编码为 {usd|cny}-{480p|720p|1080p}-{silent|audio}。原生同步音频明确包含才用 audio；视频本身不生成音频用 silent；不清楚则 null。若官网对同一精确模型明确写明音视频联合生成/有声视频，且计费公式不含音频因子、该模型价格表也未按音频开关另行定价，则视为固定场景秒价已含原生音频，该场景可且只可归入 audio，不得复制到 silent。不同版本之间绝不继承音频能力。comparison_price_type 与其它客观字段齐全时系统会确定分组，不要因实际采用动态 token 结算而遗漏官网直接公布的固定场景秒价类型。已公告弃用/EOL 仍可按当前有效价格分组，但必须在 price_basis 和 note 写明日期。
   - comparison_resolution：进入柱图时必须填 480p/720p/1080p 并与 comparison_group 一致；不进柱图填 null。若同一模型/分辨率只有默认有声固定场景价，不要仅为记录可关闭音频而另造一个无秒价的 silent 重复项，只在有独立官网价格或固定示例时拆分音频档。
   - lifecycle_status / sunset_at：按官网填写 active/deprecated/sunsetting/legacy-existing-only/discontinued/self-host-only；官网未说明生命周期时填 unknown；有正式停用日必须填 YYYY-MM-DD。
   - price_basis：必须写清模型、模式、分辨率、原生音频口径与计费档。
   - resolution、duration、frame_rate、aspect_ratios、native_audio、free_quota：仅按官网原文。
   - note：限制、附加音频/高清费用、排队方式或 API 状态等客观说明，不超过 120 字。price_per_second 或 pricing 使用限时折扣/活动价时，每个受影响档位必须写出官网完整起止日期、时间与时区，不得只写“限时”或“阶段性”。
4. 不把第三方托管价格当作厂商官方 API 价；不同分辨率、音频能力和币种绝不混组；不做主观质量评分。
5. 有效页面明确没有/已停用生视频产品时 has_video_generation=false 且 offerings 留空。无效壳页同样留空，但必须 page_has_relevant_content=false。页面没写的字段填 null/空数组，严禁猜测。"""


PLANS_SYSTEM = """你是一个严谨的大模型厂商官网套餐页解析器。

规则:
1. 只抽取页面同时明确写出「价格」和「使用额度」的套餐。
2. 额度必须是官网原文直接标注的 Token、积分、请求数、相对倍数、TPM 或「无限」。
3. 严禁自行换算：不把 prompt 换成请求数，不把周额度推成月额度，不把积分估算成 Token。
4. 官网若使用「约」「最多」「5x」等表述，value 必须保留这些限定词，note 说明官网口径。
5. 价格、币种、计费周期、有效期和额度单位保留原文，不换算汇率。
6. 不抽取只写「更多」「更高限额」但没有任何明确额度的套餐。
7. source_url 只能使用页面文本中给出的官网链接；没有时留 null。
8. 如果页面没有符合上述条件的套餐，plans 留空且 page_has_plans=false。"""


def has_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def _client() -> openai.OpenAI:
    kwargs = {"timeout": 240.0}
    base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    if base:
        kwargs["base_url"] = base
    return openai.OpenAI(**kwargs)


def _loads_json(text: str):
    """解析模型输出; 兼容个别端点在 JSON 外包 ``` 围栏或夹带说明文字。"""
    try:
        return json.loads(text)
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError("输出中找不到 JSON 对象")


def _create(client: openai.OpenAI, messages: list):
    global _MINIMAL_PARAMS
    try:
        if _MINIMAL_PARAMS:
            return client.chat.completions.create(model=MODEL, messages=messages)
        return client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
            max_completion_tokens=MAX_OUTPUT_TOKENS,
        )
    except openai.BadRequestError:
        # 端点不认 response_format / max_completion_tokens: 降级为最小参数集
        if _MINIMAL_PARAMS:
            raise ExtractionError("请求被拒绝(400), 检查 OPENAI_MODEL 是否为该端点支持的模型") from None
        _MINIMAL_PARAMS = True
        try:
            return client.chat.completions.create(model=MODEL, messages=messages)
        except Exception as exc:
            raise ExtractionError(f"请求被拒绝(400): {exc}") from exc
    except openai.RateLimitError as exc:
        raise ExtractionError("限流(429), SDK 自动重试后仍失败") from exc
    except openai.AuthenticationError as exc:
        raise ExtractionError("认证失败: 检查 OPENAI_API_KEY") from exc
    except openai.APIStatusError as exc:
        raise ExtractionError(f"API 错误({exc.status_code}): {exc.message}") from exc
    except openai.APIConnectionError as exc:
        raise ExtractionError(f"网络错误: {exc}") from exc
    except ExtractionError:
        raise
    except Exception as exc:  # 不能让单厂商异常拖垮整轮
        raise ExtractionError(f"{type(exc).__name__}: {exc}") from exc


def _parse(client: openai.OpenAI, system: str, user_text: str,
           output_type: type[BaseModel]) -> BaseModel:
    schema = json.dumps(output_type.model_json_schema(), ensure_ascii=False)
    messages = [
        {"role": "system", "content":
            f"{system}\n\n只输出一个 JSON 对象, 结构必须符合下面的 JSON Schema"
            f"(页面没提到的值一律填 null, 不要编造):\n{schema}"},
        {"role": "user", "content": user_text},
    ]
    last_err: Exception = ValueError("no output")
    for _ in range(2):
        resp = _create(client, messages)
        text = (resp.choices[0].message.content or "").strip()
        try:
            return output_type.model_validate(_loads_json(text))
        except (ValueError, ValidationError) as exc:
            last_err = exc
            messages += [
                {"role": "assistant", "content": text[:4000]},
                {"role": "user", "content":
                    f"上面的 JSON 未通过 Schema 校验: {str(exc)[:500]}\n"
                    "请重新输出修正后的完整 JSON 对象。"},
            ]
    raise ExtractionError(f"结构化输出校验失败: {str(last_err)[:300]}")


def _page_text_header(provider: str, url: str, page_text: str) -> str:
    head = f"厂商: {provider}\nURL: {url}\n"
    if len(page_text) > MAX_PAGE_CHARS:
        head += f"(页面文本超过 {MAX_PAGE_CHARS} 字符, 已截断)\n"
    return f"{head}\n<page>\n{page_text[:MAX_PAGE_CHARS]}\n</page>\n"


def extract_pricing(provider: str, url: str, page_text: str) -> PricingPage:
    """抽取一个厂商价格页。失败抛 ExtractionError。"""
    client = _client()
    user_text = _page_text_header(provider, url, page_text) + "\n请抽取价格表。"
    parsed = _parse(client, PRICING_SYSTEM, user_text, PricingPage)
    # 清洗明显异常的行: 空名, 或输入输出都没价(通常是表头/误识别)
    parsed.models = [
        m for m in parsed.models
        if m and m.model and m.model.strip()
        and (m.input_per_1m is not None or m.output_per_1m is not None)
    ]
    return parsed


def extract_news(provider: str, url: str, page_text: str) -> NewsPage:
    """抽取一个厂商公告页。失败抛 ExtractionError。"""
    client = _client()
    user_text = _page_text_header(provider, url, page_text) + "\n请抽取公告条目。"
    return _parse(client, NEWS_SYSTEM, user_text, NewsPage)


def extract_plans(provider: str, url: str, page_text: str) -> PlansPage:
    """抽取官网明示价格与额度的套餐，失败抛 ExtractionError。"""
    client = _client()
    user_text = _page_text_header(provider, url, page_text) + "\n请抽取套餐与额度。"
    parsed = _parse(client, PLANS_SYSTEM, user_text, PlansPage)
    parsed.plans = [p for p in parsed.plans if p.name.strip() and p.quotas]
    return parsed


def extract_websearch(provider: str, url: str, page_text: str) -> WebSearchPage:
    """抽取官网联网搜索能力与定价的客观事实，失败抛 ExtractionError。"""
    client = _client()
    user_text = _page_text_header(provider, url, page_text) + "\n请抽取联网搜索能力与定价。"
    parsed = _parse(client, WEBSEARCH_SYSTEM, user_text, WebSearchPage)
    parsed.offerings = [o for o in parsed.offerings if o.name and o.name.strip()]
    return parsed


def extract_imagegen(provider: str, url: str, page_text: str) -> ImageGenerationPage:
    """抽取官网生图能力、规格与可比价格，失败抛 ExtractionError。"""
    client = _client()
    user_text = _page_text_header(provider, url, page_text) + "\n请抽取生图 API 事实。"
    parsed = _parse(client, IMAGEGEN_SYSTEM, user_text, ImageGenerationPage)
    parsed.offerings = [o for o in parsed.offerings if o.name and o.name.strip()]
    return parsed


def extract_videogen(provider: str, url: str, page_text: str) -> VideoGenerationPage:
    """抽取官网生视频能力、规格与可比价格，失败抛 ExtractionError。"""
    client = _client()
    user_text = _page_text_header(provider, url, page_text) + "\n请抽取生视频 API 事实。"
    parsed = _parse(client, VIDEOGEN_SYSTEM, user_text, VideoGenerationPage)
    parsed.offerings = [o for o in parsed.offerings if o.name and o.name.strip()]
    return parsed
