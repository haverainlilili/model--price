"""结构化抽取结果的 Pydantic 模型（同时生成提示词中的 JSON Schema）。"""
from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


def _validate_iso_calendar_date(value: Optional[str]) -> Optional[str]:
    if value is not None:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be a real ISO calendar date (YYYY-MM-DD)") from exc
    return value


ImageGenerationMode = Literal[
    "text-to-image", "image-to-image", "image-edit", "reference",
    "inpainting", "outpainting",
]
VideoGenerationMode = Literal[
    "text-to-video", "image-to-video", "first-last-frame",
    "reference", "reference-to-video", "video-edit", "video-to-video",
]


class StrictGenerationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageGenerationOffering(StrictGenerationModel):
    """一项生图模型/API 的官网客观事实。"""

    name: str = Field(..., description="模型或生图产品名原文")
    variant_key: Optional[str] = Field(None, description="同模型拆分区域/质量/队列/模式时的稳定客观档位键")
    api_available: Optional[bool] = Field(
        None, description="官网是否明确提供公开 API；未说明则 null")
    modes: List[ImageGenerationMode] = Field(
        default_factory=list,
        description="官网明确支持的模式，如 text-to-image / image-edit / reference")
    pricing: Optional[str] = Field(None, description="官网定价原文或忠实简写")
    currency: Optional[str] = Field(None, description="USD / CNY / EUR 等原始币种")
    region: Optional[Literal["intl", "domestic"]] = Field(
        None, description="该独立区域价/部署端点属于国际或中国国内；无区域差异则 null")
    price_per_image: Optional[float] = Field(
        None, ge=0, allow_inf_nan=False, description="指定公开档位的原币种单张价格；不能机械换算则 null")
    comparison_group: Optional[Literal[
        "usd-standard-1mp", "cny-standard-1mp",
        "usd-premium-1mp", "cny-premium-1mp",
    ]] = Field(
        None, description="严格可比组：币种 × standard/premium × 约 1MP；不满足则 null")
    price_basis: Optional[str] = Field(
        None, description="单张价格对应的模型、质量、分辨率和计费档位")
    resolution: Optional[str] = Field(None, description="官网明确的输出分辨率")
    aspect_ratios: Optional[str] = Field(None, description="官网明确支持的宽高比")
    output_formats: Optional[str] = Field(None, description="官网明确的输出格式")
    comparison_width: Optional[int] = Field(
        None, gt=0, description="仅可比柱图对应输出的精确像素宽；不进柱图则 null")
    comparison_height: Optional[int] = Field(
        None, gt=0, description="仅可比柱图对应输出的精确像素高；不进柱图则 null")
    quality_tier: Optional[Literal["standard", "premium"]] = Field(
        None, description="仅可比柱图的 standard/premium 客观价档；不进柱图则 null")
    lifecycle_status: Literal[
        "active", "deprecated", "sunsetting", "legacy-existing-only", "discontinued", "self-host-only", "unknown",
    ] = Field("unknown", description="该型号/API 当前官方生命周期状态；官网未说明则 unknown")
    sunset_at: Optional[str] = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="已公告停止服务日 YYYY-MM-DD")
    free_quota: Optional[str] = Field(None, description="官网明确的免费额度")
    note: Optional[str] = Field(None, description="限制、附加费用或其它客观事实")

    @field_validator("price_per_image", mode="before")
    @classmethod
    def reject_boolean_image_price(cls, value):
        if isinstance(value, bool):
            raise ValueError("price_per_image must be numeric, not boolean")
        return value

    @field_validator("sunset_at")
    @classmethod
    def validate_sunset_at(cls, value):
        return _validate_iso_calendar_date(value)

    @model_validator(mode="after")
    def validate_comparison_contract(self):
        group = self.comparison_group
        if group is None:
            if any(value is not None for value in (
                    self.comparison_width, self.comparison_height, self.quality_tier)):
                raise ValueError("comparison dimensions/tier require comparison_group")
            return self
        expected_currency = group.split("-", 1)[0].upper()
        expected_tier = "premium" if "-premium-" in group else "standard"
        width, height = self.comparison_width, self.comparison_height
        if (self.price_per_image is None or self.api_available is not True
                or (self.currency or "").upper() != expected_currency
                or width is None or height is None or width != height
                or not 800_000 <= width * height <= 1_500_000
                or self.quality_tier != expected_tier
                or not (self.price_basis or "").strip()):
            raise ValueError("image comparison_group facts are incomplete or inconsistent")
        return self


class ImageGenerationPage(StrictGenerationModel):
    """一个厂商官网生图能力/定价页的抽取结果。"""

    page_has_relevant_content: bool = Field(
        ..., description="页面是有效产品/价格/停用说明，而非报错、登录或人机验证壳")
    product_status: Literal["active", "discontinued", "unknown"] = Field(
        ..., description="产品整体状态；正式停止时为 discontinued")
    product_status_date: Optional[str] = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="整体停用生效日 YYYY-MM-DD")
    product_status_note: Optional[str] = Field(
        None, description="官网整体状态或停用说明，不超过 120 字")
    has_image_generation: bool = Field(..., description="该厂商是否提供生图产品")
    offerings: List[ImageGenerationOffering] = Field(default_factory=list)

    @field_validator("product_status_date")
    @classmethod
    def validate_product_status_date(cls, value):
        return _validate_iso_calendar_date(value)


class VideoGenerationOffering(StrictGenerationModel):
    """一项生视频模型/API 的官网客观事实。"""

    name: str = Field(..., description="模型或生视频产品名原文")
    variant_key: Optional[str] = Field(None, description="同模型拆分区域/分辨率/音频/队列/模式时的稳定客观档位键")
    api_available: Optional[bool] = Field(
        None, description="官网是否明确提供公开 API；未说明则 null")
    modes: List[VideoGenerationMode] = Field(
        default_factory=list,
        description="官网明确支持的模式，如 text-to-video / image-to-video / first-last-frame")
    pricing: Optional[str] = Field(None, description="官网定价原文或忠实简写")
    currency: Optional[str] = Field(None, description="USD / CNY / EUR 等原始币种")
    region: Optional[Literal["intl", "domestic"]] = Field(
        None, description="该独立区域价/部署端点属于国际或中国国内；无区域差异则 null")
    price_per_second: Optional[float] = Field(
        None, ge=0, allow_inf_nan=False, description="指定公开档位的原币种每生成秒价格；不能机械换算则 null")
    comparison_group: Optional[Literal[
        "usd-480p-silent", "usd-480p-audio",
        "usd-720p-silent", "usd-720p-audio",
        "usd-1080p-silent", "usd-1080p-audio",
        "cny-480p-silent", "cny-480p-audio",
        "cny-720p-silent", "cny-720p-audio",
        "cny-1080p-silent", "cny-1080p-audio",
    ]] = Field(
        None, description="严格可比组，编码币种、分辨率与是否含原生音频；不满足则 null")
    price_basis: Optional[str] = Field(
        None, description="每秒价格对应的模型、模式、分辨率、音频和公开档位")
    resolution: Optional[str] = Field(None, description="官网明确的输出分辨率")
    duration: Optional[str] = Field(None, description="官网明确支持的片段时长")
    frame_rate: Optional[str] = Field(None, description="官网明确的帧率")
    aspect_ratios: Optional[str] = Field(None, description="官网明确支持的宽高比")
    native_audio: Optional[bool] = Field(
        None, description="是否由该模型原生生成同步音频；官网未说明则 null")
    comparison_resolution: Optional[Literal["480p", "720p", "1080p"]] = Field(
        None, description="仅可比柱图采用的精确输出档；不进柱图则 null")
    lifecycle_status: Literal[
        "active", "deprecated", "sunsetting", "legacy-existing-only", "discontinued", "self-host-only", "unknown",
    ] = Field("unknown", description="该型号/API 当前官方生命周期状态；官网未说明则 unknown")
    sunset_at: Optional[str] = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="已公告停止服务日 YYYY-MM-DD")
    free_quota: Optional[str] = Field(None, description="官网明确的免费额度")
    note: Optional[str] = Field(None, description="限制、附加费用或其它客观事实")

    @field_validator("price_per_second", mode="before")
    @classmethod
    def reject_boolean_video_price(cls, value):
        if isinstance(value, bool):
            raise ValueError("price_per_second must be numeric, not boolean")
        return value

    @field_validator("sunset_at")
    @classmethod
    def validate_sunset_at(cls, value):
        return _validate_iso_calendar_date(value)

    @model_validator(mode="after")
    def validate_comparison_contract(self):
        group = self.comparison_group
        if group is None:
            if self.comparison_resolution is not None:
                raise ValueError("comparison_resolution requires comparison_group")
            return self
        expected_currency, expected_resolution, audio_kind = group.split("-")
        expected_audio = audio_kind == "audio"
        if (self.price_per_second is None or self.api_available is not True
                or (self.currency or "").lower() != expected_currency
                or self.comparison_resolution != expected_resolution
                or self.native_audio is not expected_audio
                or not (self.price_basis or "").strip()):
            raise ValueError("video comparison_group facts are incomplete or inconsistent")
        return self


class VideoGenerationPage(StrictGenerationModel):
    """一个厂商官网生视频能力/定价页的抽取结果。"""

    page_has_relevant_content: bool = Field(
        ..., description="页面是有效产品/价格/停用说明，而非报错、登录或人机验证壳")
    product_status: Literal["active", "discontinued", "unknown"] = Field(
        ..., description="产品整体状态；正式停止时为 discontinued")
    product_status_date: Optional[str] = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="整体停用生效日 YYYY-MM-DD")
    product_status_note: Optional[str] = Field(
        None, description="官网整体状态或停用说明，不超过 120 字")
    has_video_generation: bool = Field(..., description="该厂商是否提供生视频产品")
    offerings: List[VideoGenerationOffering] = Field(default_factory=list)

    @field_validator("product_status_date")
    @classmethod
    def validate_product_status_date(cls, value):
        return _validate_iso_calendar_date(value)


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
