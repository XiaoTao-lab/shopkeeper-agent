"""
SQL 执行节点

负责执行最终 SQL，并产出查询结果。
它不只返回数据，还附带几类"让人用得下去"的信息：

- **一句话结论**（``summary``）：把结果表压成一句人话，用户不点开表格也能抓到重点；
- **图表说明书**（``chart``）：用纯规则判断这张表该配什么图（时间→折线、对比→柱状），
  前端照着渲染，让"谁高谁低、是涨是跌"一眼可见；判定不该出图时不下发；
- **可解释信息**（``explain``）：系统把问题理解成了什么、用了哪个指标口径、实际执行的 SQL、
  耗时多久——让"这个数可不可信"变得可验证；
- **权限提示**（``permission``）：本轮以什么角色执行、是否做了行级过滤 / 列级脱敏，
  让"为什么我只看到这些数据"一目了然。

执行完成后流程进入 finalize（写审计）再结束。
"""

import time

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, MetricInfoState
from app.conf.app_config import app_config
from app.core.chart_planner import plan_chart
from app.core.log import logger
from app.core.mask import mask_rows
from app.core.result_guard import build_empty_notice
from app.core.result_snapshot import build_snapshot
from app.core.result_summary import build_result_summary


def _build_explain(state: DataAgentState, sql: str) -> dict:
    """组装"系统是怎么理解并回答这个问题的"可解释信息"""

    metric_infos: list[MetricInfoState] = state.get("metric_infos") or []
    started_at = state.get("started_at")
    elapsed_ms = int(time.time() * 1000) - started_at if started_at else None

    return {
        # 系统把用户问题理解成了什么（多轮改写后的独立问题）
        "rewritten_query": state.get("rewritten_query") or state.get("query") or "",
        # 命中的指标与其冻结口径（只列有标准计算式的，和口径约束保持一致）
        "metrics": [
            {
                "name": metric.get("name"),
                "formula": metric.get("formula"),
                "base_table": metric.get("base_table"),
                "unit": metric.get("unit"),
            }
            for metric in metric_infos
            if metric.get("formula")
        ],
        # 实际执行的 SQL（已过沙箱加固与行级权限注入）
        "sql": sql,
        # 解析出的时间区间
        "time_range": state.get("time_range"),
        "elapsed_ms": elapsed_ms,
    }


async def run_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    """执行 SQL 并产出最终问数结果"""

    writer = runtime.stream_writer
    step = "执行 SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    try:
        # 这里拿到的可能是 generate_sql 直接通过校验的 SQL，
        # 也可能是 correct_sql 覆盖后再经 inject_rls 注入行级权限的 SQL
        sql = state["sql"]
        dw_mysql_repository = runtime.context["dw_mysql_repository"]

        # 真实数据库访问统一封装在仓储层，节点只负责从状态取 SQL 并触发执行
        # run_with_columns 返回有序列名和原生 Python 值，保持查询投影顺序
        columns, result = await dw_mysql_repository.run_with_columns(sql)
        logger.info(f"SQL 执行完成：{len(result)} 行")

        # 列级脱敏：按当前角色对命中字段打码，脱敏只影响返回给用户的副本，
        # 不影响"到底查到多少行"的事实
        role_name = state.get("user_role") or app_config.rbac.default_role
        role_cfg = app_config.rbac.resolve(role_name)
        masked = mask_rows(result, role_cfg.mask_columns)
        display_result = masked.rows

        # 构建结构化快照：有序列、类型推断、预算检查
        snapshot = build_snapshot(
            columns=columns,
            rows=display_result,
            masked_columns=masked.masked_columns,
        )

        # 空结果（0 行，或聚合落在空集上得到全 None）单独给出提示，
        # 避免前端把 NULL 渲染成一个 '-' 让人误以为功能坏了
        notice = build_empty_notice(display_result, state.get("time_range"))
        if notice:
            logger.info(f"空结果提示：{notice}")
            snapshot["empty"] = True
            snapshot["message"] = notice
        else:
            # 有数据时给一句确定性结论（极值/组数），纯规则生成，与表格严格一致
            summary = build_result_summary(display_result)
            if summary:
                snapshot["summary"] = summary

            # 再判断该配什么图（同样是纯规则，不调大模型）。
            # 没有合适图型时不下发 chart 字段，前端自然不会渲染图表。
            chart = plan_chart(display_result, state.get("metric_infos"))
            if chart:
                snapshot["chart"] = chart

        # 无论有没有数据，都附带可解释信息与权限提示
        snapshot["explain"] = _build_explain(state, sql)
        snapshot["permission"] = {
            "role": role_name,
            "rls_applied": bool(state.get("rls_applied")),
            "region_ids": state.get("rls_region_ids") or [],
            "masked_columns": masked.masked_columns,
        }

        writer({"type": "progress", "step": step, "status": "success"})
        writer(snapshot)

        # 把本轮 SQL 作为 AI 消息写回对话历史（add_messages 追加），
        # 供下一轮"多轮问题改写"理解上一轮到底查了什么指标、维度和时间。
        # 同时把行数与脱敏列写回 state，供 finalize 写审计。
        return {
            "messages": [AIMessage(content=sql)],
            "row_count": len(result),
            "masked_columns": masked.masked_columns,
        }

    except Exception as e:
        logger.error(f"{step} failed: {e}")
        writer({"type": "progress", "step": step, "status": "error"})
        raise
