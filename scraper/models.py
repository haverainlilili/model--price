"""结构化抽取结果的 Pydantic 模型（同时生成提示词中的 JSON Schema）。"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ModelPrice(BaseModel):
    """单个模型的按量价格, 单位: 每百万 tokens。"""

    model: str = Field(..., description="模型名, 保留页面原文, 如 GPT-5.2 / qwen3-max")
    input_per_1m: Optional[float] = Field(
        None, description="输入价格(每百万 tokens), 页面原币种; 没有则 null")
    output_per_1m: Optional[float] = Field(
        None, description="输出价格(每百万 tokens), 页面原币种; 没有则 null")
    cached_input_per_1m: Optional[float] = Field(
        None, description="缓存命中的输入价格(每百万 tokens); 没有则 null")
    currency: Optional[str] = Field(
        None, description="该价格使用的币种: USD / CNY / EUR")
    note: Optional[str] = Field(
        None, description="备注: 限时折扣、免费额度、档位、模型类型(如 embedding)等")


class PricingPage(BaseModel):
    """一个厂商价格页的完整抽取结果。"""

    currency: Optional[str] = Field(
        None, description="该页价格的主要币种: USD / CNY / EUR")
    models: List[ModelPrice] = Field(default_factory=list, description="模型价格列表")
    promotions: Optional[str] = Field(
        None, description="页面上明显的促销/活动文字整段, 没有则 null")
    page_has_pricing: bool = Field(
        True, description="页面是否包含可解析的 API 价格表; JS 空壳/报错页为 false")


class PlanQuota(BaseModel):
    """官网直接标注的一项套餐额度，保留原文单位和窗口。"""

    label: str = Field(..., description="额度名称，如 M3 编程调用 / 周积分")
    value: str = Field(..., description="官网标注数值原文，如 约 12,000 次 / 60,000 积分")
    window: Optional[str] = Field(
        None, description="刷新或有效窗口，如 每 5 小时 / 每周 / 每月")


class SubscriptionPlan(BaseModel):
    """官网公开的订阅、资源包或企业容量套餐。"""

    name: str = Field(..., description="套餐名称原文")
    plan_type: Optional[str] = Field(
        None, description="类型：聊天会员 / Coding Plan / Token Plan / API 资源包等")
    price: str = Field(..., description="官网标注价格原文，不自行换算")
    billing: Optional[str] = Field(None, description="计费周期或有效期")
    quotas: List[PlanQuota] = Field(
        default_factory=list, description="官网直接标注的额度，禁止推算")
    models: List[str] = Field(default_factory=list, description="官网明示的支持模型")
    note: Optional[str] = Field(None, description="适用范围、限流或官网估算口径")
    source_url: Optional[str] = Field(None, description="套餐对应的官网链接")


class PlansPage(BaseModel):
    """一个厂商官网套餐页的完整抽取结果。"""

    plans: List[SubscriptionPlan] = Field(default_factory=list)
    page_has_plans: bool = Field(
        True, description="页面是否同时公开套餐价格和可用额度")


class WebSearchOffering(BaseModel):
    """厂商联网搜索能力的一条客观事实记录。"""

    name: str = Field(..., description="搜索能力/产品名原文, 如 Search API / Grounding with Google Search / 联网搜索")
    pricing: Optional[str] = Field(
        None, description="定价或计费方式原文, 如 免费 / 按 token 计费 / $5 per 1000 requests")
    price_per_1k_usd: Optional[float] = Field(
        None, ge=0, description="可直接比较的美元基础价/千次请求；官网不能精确换算则 null")
    price_basis: Optional[str] = Field(
        None, description="该千次价格对应档位与口径，如 PAYG basic / Starter / queue normal")
    free_quota: Optional[str] = Field(
        None, description="官网明确标注的免费额度原文；没有或未说明则 null")
    output_type: Optional[str] = Field(
        None, description="官网描述的输出形态，如结构化搜索结果 / 带引用答案 / 模型工具")
    cites_sources: Optional[bool] = Field(
        None, description="响应是否含来源 URL 或可点击引用; 页面明确说明才填 true/false, 没提则 null")
    default_on: Optional[bool] = Field(
        None, description="是否默认开启(无需手动启用或调用); 页面明确说明才填 true/false, 没提则 null")
    note: Optional[str] = Field(
        None, description="补充的客观说明原文(搜索深度、结果数量、可用区域、限制等)")


class WebSearchPage(BaseModel):
    """一个厂商官网联网搜索能力/定价页的完整抽取结果。"""

    has_search: bool = Field(..., description="该厂商是否提供联网/网络搜索能力")
    offerings: List[WebSearchOffering] = Field(
        default_factory=list, description="搜索能力条目(通常 1 条; 有多个产品时多条)")


class NewsEntry(BaseModel):
    """一条官方公告。"""

    date: Optional[str] = Field(None, description="页面标注的日期原文, 如 2026-08-20")
    title: str = Field(..., description="公告标题原文")
    url: Optional[str] = Field(None, description="该条目在页面文本中的链接, 没有则 null")
    summary: Optional[str] = Field(None, description="不超过 60 字的中文一句话摘要")


class NewsPage(BaseModel):
    """一个厂商公告页的抽取结果。"""

    entries: List[NewsEntry] = Field(
        default_factory=list, description="最近公告, 最多 12 条")
