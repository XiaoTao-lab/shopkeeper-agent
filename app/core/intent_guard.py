"""
意图安全校验

在问数链路入口判断用户问题是否包含「写操作」意图（修改 / 删除 / 新增数据）。
采用「规则预筛 + LLM 兜底」的混合策略：

- 规则层：命中高置信写动词或句式时直接判定，省去一次 LLM 调用
- LLM 层：规则未命中时，用大模型理解隐含的写意图（例如"帮我把滞销商品下架"）

目标是拦住"用户想改数据"的请求，避免后续链路用一条伪造的查询去迎合用户，
从而在 SQL 安全沙箱之外补齐「意图层」这一道防线。

本模块不依赖数据库连接；LLM 通过参数注入，便于单元测试与复用。
"""

import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate

from app.core.log import logger
from app.prompt.prompt_loader import load_prompt


class IntentResult:
    """意图校验结果。

    Attributes:
        is_write: 是否为写操作意图
        reason: 判定原因
        source: 判定来源，rule（规则层）/ llm（模型层）
    """

    __slots__ = ("is_write", "reason", "source")

    def __init__(self, is_write: bool, reason: str, source: str):
        self.is_write = is_write
        self.reason = reason
        self.source = source


# 高置信写操作动词 / 句式。
# 只放语义明确、几乎不会出现在正常问数里的词；
# "新增 / 添加 / 更新 / 变更 / 下架" 等有歧义的词（如"统计新增用户数"）交给 LLM 层判断。
_WRITE_KEYWORDS = (
    "删除",
    "删掉",
    "清空",
    "清除",
    "移除",
    "修改",
    "改成",
    "改为",
    "设为",
    "设置为",
    "替换",
    "写入",
    "录入",
    "插入",
)
_WRITE_REGEX = re.compile(
    "|".join(_WRITE_KEYWORDS)
    + r"|\bdrop\b|\bdelete\b|\btruncate\b|\bupdate\b|\binsert\b|\balter\b",
    re.IGNORECASE,
)


def match_write_by_rule(query: str) -> str | None:
    """规则层判定：命中高置信写动词时返回该关键词，否则返回 None。

    Args:
        query: 用户原始问题

    Returns:
        命中的写操作关键词；未命中返回 None
    """
    match = _WRITE_REGEX.search(query or "")
    return match.group(0) if match else None


async def _classify_by_llm(query: str, llm: BaseChatModel) -> tuple[bool, str]:
    """用 LLM 判断问题意图。

    Args:
        query: 用户原始问题
        llm: 语言模型实例

    Returns:
        (是否为写意图, 判定原因)
    """
    prompt = PromptTemplate(
        template=load_prompt("check_intent"),
        input_variables=["query"],
    )
    # 提示词要求模型只输出 JSON 对象，这里直接解析成 dict
    chain = prompt | llm | JsonOutputParser()
    data: dict[str, Any] = await chain.ainvoke({"query": query})
    return bool(data.get("is_write", False)), str(data.get("reason", ""))


async def check_intent(query: str, llm: BaseChatModel | None = None) -> IntentResult:
    """判断用户问题是否属于写操作意图。

    Args:
        query: 用户原始问题
        llm: 用于语义判别的模型；为 None 时跳过 LLM 层

    Returns:
        IntentResult: 意图判定结果
    """
    # 规则层：高置信写动词直接判定，省一次 LLM 调用
    keyword = match_write_by_rule(query)
    if keyword:
        return IntentResult(
            is_write=True,
            reason=f"检测到写操作关键词「{keyword}」",
            source="rule",
        )

    # LLM 层：规则未命中时，交给模型理解隐含的写意图
    if llm is None:
        return IntentResult(is_write=False, reason="未命中规则且无 LLM，默认放行", source="rule")

    try:
        is_write, reason = await _classify_by_llm(query, llm)
    except Exception as e:  # noqa: BLE001
        # 判别失败不能阻断正常问数，保守放行并记录，避免把查询误拦成不可用
        logger.warning(f"意图判别失败，默认放行：{e}")
        return IntentResult(is_write=False, reason=f"判别失败：{e}", source="llm")

    return IntentResult(is_write=is_write, reason=reason, source="llm")
