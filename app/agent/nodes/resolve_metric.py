"""
指标口径冻结节点（语义层落地）

过滤节点选出了问题涉及的指标，但指标元数据里既有"描述"也有"标准计算式"。
本节点负责把已选指标编译成一段**权威口径约束文本**，随 SQL 生成提示词一起下发。

为什么需要这一步：把口径从"自然语言描述"升级为"冻结的可执行表达式"。
模型拿到的不再是"GMV 是成交金额总和"这种需要自行翻译的描述，
而是 SUM(fact_order.order_amount) 这样一条不可改写的标准计算式，
从源头消除同一问题两次算出不同口径的"口径漂移"。

本节点为纯上下文处理，不访问数据库与模型，便于离线单元测试。
"""

from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, MetricInfoState
from app.core.log import logger

_STEP = "冻结指标口径"


def compile_metric_caliber(metric_infos: list[MetricInfoState]) -> str:
    """把已选中的指标编译成权威口径约束文本。

    Args:
        metric_infos: 过滤后的指标上下文列表

    Returns:
        口径约束文本；若没有任何带标准计算式的指标则返回空串
    """

    # 只有标注了标准计算式的指标才纳入约束，避免下发空口径干扰模型
    valid = [m for m in metric_infos if m.get("formula")]
    if not valid:
        return ""

    lines = [
        "【指标口径约束】以下指标的计算口径已冻结，涉及这些指标时必须严格使用"
        "标准计算式，禁止自行改写、拆分或重新定义口径。"
    ]
    base_tables: set[str] = set()
    for metric in valid:
        lines.append(f"- {metric['name']}（单位：{metric.get('unit') or '未标注'}）")
        lines.append(f"  标准计算式：{metric['formula']}")
        if metric.get("base_table"):
            lines.append(f"  主表：{metric['base_table']}")
            base_tables.add(metric["base_table"])
        if metric.get("time_column"):
            lines.append(f"  默认时间字段：{metric['time_column']}")
        filters = metric.get("default_filters") or []
        lines.append(f"  默认过滤：{'；'.join(filters) if filters else '无'}")
        if metric.get("description"):
            lines.append(f"  口径说明：{metric['description']}")

    # 多指标跨表时给出关联提示，避免模型漏掉公共维度关联
    if len(base_tables) > 1:
        lines.append(
            f"注意：所选指标跨越多个主表（{'、'.join(sorted(base_tables))}），"
            "计算时需通过公共维度字段正确关联。"
        )

    return "\n".join(lines)


async def resolve_metric(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """把已选指标编译成冻结口径文本，写入状态供 SQL 生成节点使用"""

    writer = runtime.stream_writer
    writer({"type": "progress", "step": _STEP, "status": "running"})

    try:
        metric_infos: list[MetricInfoState] = state.get("metric_infos") or []
        caliber_text = compile_metric_caliber(metric_infos)

        if caliber_text:
            frozen = [m["name"] for m in metric_infos if m.get("formula")]
            logger.info(f"冻结指标口径：{frozen}")
        else:
            logger.info("未匹配到带标准计算式的指标，跳过口径约束")

        writer({"type": "progress", "step": _STEP, "status": "success"})
        return {"metric_caliber_text": caliber_text}

    except Exception as e:
        logger.error(f"{_STEP} failed: {e}")
        writer({"type": "progress", "step": _STEP, "status": "error"})
        raise
