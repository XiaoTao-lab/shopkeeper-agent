"""
时间维度解析节点

把用户问题里的"上个月""本季度""最近7天"等时间表达，解析成确定性的半开区间 [start, end)，
随 SQL 生成提示词一起下发，避免把日期推算交给大模型（跨年、闰年、含不含今天都是高发错点）。

时间字段的来源：指标口径里的"默认时间字段"，或时间维度表 dim_date 的对应字段。
本节点与 resolve_metric 同属"上下文冻结"：一个冻结指标口径，一个冻结时间区间。

容错策略：解析失败不加时间约束（宁可少约束，也不下发错误区间），不阻断问数链路。
"""

from dataclasses import asdict
from datetime import date

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.state import DataAgentState
from app.core.log import logger
from app.core.time_parser import TimeRange, parse_time_range

_STEP = "解析时间范围"


def _resolve_today(date_info: dict | None) -> date:
    """从 date_info 取"今天"，取不到时回退到系统当前日期"""
    raw = (date_info or {}).get("date")
    try:
        return date.fromisoformat(raw) if raw else date.today()
    except (TypeError, ValueError):
        return date.today()


def render_time_range_text(time_range: TimeRange) -> str:
    """把时间区间渲染成权威约束文本（纯函数，便于单测）"""
    return (
        "【时间范围约束】用户问题中的时间表达已解析为半开区间（含起始、不含结束），"
        "请直接据此做时间过滤，不要自行推算日期：\n"
        f"- 原始表达：{time_range.expr}\n"
        f"- 起始（含）：{time_range.start}\n"
        f"- 结束（不含）：{time_range.end}\n"
        f"- 粒度：{time_range.granularity}\n"
        "时间字段优先使用【指标口径约束】中给出的默认时间字段，"
        "或时间维度表 dim_date 的对应字段。"
    )


async def resolve_time(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """解析问题中的时间表达，写入 time_range 与 time_range_text"""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": _STEP, "status": "running"})

    try:
        query = state["query"]
        today = _resolve_today(state.get("date_info"))
        time_range = await parse_time_range(query, today, llm)

        # 无时间表达：不加约束，链路继续
        if time_range is None:
            logger.info("未解析到时间表达，跳过时间约束")
            writer({"type": "progress", "step": _STEP, "status": "success"})
            return {"time_range": None, "time_range_text": ""}

        logger.info(
            f"解析时间区间：{time_range.start} ~ {time_range.end}"
            f"（{time_range.expr}，来源={time_range.source}）"
        )
        writer({"type": "progress", "step": _STEP, "status": "success"})
        return {
            "time_range": asdict(time_range),
            "time_range_text": render_time_range_text(time_range),
        }

    except Exception as e:
        # 时间解析失败不应阻断问数，降级为"无时间约束"
        logger.error(f"{_STEP} failed，降级为不加时间约束：{e}")
        writer({"type": "progress", "step": _STEP, "status": "success"})
        return {"time_range": None, "time_range_text": ""}
