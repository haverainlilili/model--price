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

from .models import NewsPage, PricingPage

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
7. model 保留页面上的模型名原文。同一模型不同上下文档位价格不同时拆成多行, 在 note 标注档位。
8. promotions 汇总页面上明显的促销/活动文字(整段抄录), 没有则为 null。
9. 如果页面文本不含价格表(如 JS 渲染的空壳、报错页、人机验证页), 把 page_has_pricing 设为 false 且 models 留空。"""


NEWS_SYSTEM = """你从厂商官方公告 / changelog / 新闻页文本中抽取最近的公告条目。

规则:
- date: 页面标注的日期, 保留原文格式(如 2026-08-20 / Aug 20, 2026); 没有则 null
- title: 公告标题原文
- url: 文本中明确属于该条目的链接才填, 否则 null
- summary: 不超过 60 字的一句话中文摘要
- 只抽与该厂商模型/产品/价格相关的条目, 最多 12 条, 按页面出现顺序(新的在前)
- 页面没有公告(空壳/报错页)则 entries 留空"""


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
