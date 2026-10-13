"""
intent_guard 意图校验单元测试

覆盖三层逻辑：
1. 规则层：高置信写动词命中 / 正常问数不误伤
2. LLM 层：模型判别写意图 / 查询意图 / 异常兜底放行
3. 关键回归：伪造 SELECT 规避场景（"把华东地区的GMV改为1"）必须在意图层被拦住

LLM 用本地假模型注入，测试不联网、不依赖真实 API Key。
"""

import json

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.core.intent_guard import check_intent, match_write_by_rule


def _fake_llm(is_write: bool, reason: str = "测试原因"):
    """构造一个假的 LLM，直接返回指定 JSON 结果，避免真实网络调用"""
    payload = json.dumps({"is_write": is_write, "reason": reason})
    return RunnableLambda(lambda _: AIMessage(content=payload))


# ---------- 规则层 ----------


def test_rule_hits_write_keyword() -> None:
    """写动词应被规则命中"""
    assert match_write_by_rule("把华东地区的GMV改为1") == "改为"


def test_rule_hits_delete() -> None:
    """删除动词应被规则命中"""
    assert match_write_by_rule("删除所有测试订单") == "删除"


def test_rule_ignores_normal_query() -> None:
    """正常问数不应命中规则"""
    assert match_write_by_rule("统计华北地区的销售总额") is None


def test_rule_hits_english_keyword() -> None:
    """英文写动词也应命中，覆盖大小写"""
    assert match_write_by_rule("DROP TABLE fact_order") is not None


# ---------- check_intent：规则短路与无 LLM 放行 ----------


def test_rule_short_circuit_blocks_write() -> None:
    """命中规则时直接判定，无需 LLM"""
    import asyncio

    result = asyncio.run(check_intent("删除所有测试订单"))
    assert result.is_write is True
    assert result.source == "rule"


def test_no_llm_normal_query_passes() -> None:
    """无 LLM 且未命中规则时默认放行"""
    import asyncio

    result = asyncio.run(check_intent("统计华北地区的销售总额"))
    assert result.is_write is False


# ---------- check_intent：LLM 层 ----------


def test_llm_detects_write_intent() -> None:
    """规则未命中时，LLM 应识别出隐含写意图"""
    import asyncio

    result = asyncio.run(
        check_intent("帮我把滞销商品下架", _fake_llm(True, "要求修改商品状态"))
    )
    assert result.is_write is True
    assert result.source == "llm"


def test_llm_allows_query() -> None:
    """LLM 判定为查询时放行"""
    import asyncio

    result = asyncio.run(
        check_intent("统计新增用户数量", _fake_llm(False, "统计新增用户数，属于查询"))
    )
    assert result.is_write is False


def test_llm_error_fails_safe() -> None:
    """LLM 判别异常时保守放行，不阻断正常问数"""

    def _boom(_):
        raise RuntimeError("模型不可用")

    import asyncio

    result = asyncio.run(check_intent("统计华北销售额", RunnableLambda(_boom)))
    assert result.is_write is False


# ---------- 关键回归 ----------


def test_fake_select_evasion_blocked_at_intent_layer() -> None:
    """回归：用户让模型把数据改成 1，意图层必须先挡住（不依赖下游 SQL 沙箱）"""
    import asyncio

    result = asyncio.run(check_intent("把华东地区的GMV改为1"))
    assert result.is_write is True
    assert result.source == "rule"
