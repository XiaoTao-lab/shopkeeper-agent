"""
查询结果空值兜底

run_sql 执行完 SQL 后，可能拿到两类"没有数据"的结果：

1. 空列表 ``[]``：条件过滤后一行都没有；
2. 单行且单元格全为 ``None``：聚合查询作用在空集上，
   例如 ``SUM`` / ``AVG`` / ``COUNT`` 之外的聚合在无匹配行时返回 ``NULL``。

这两种情况如果原样透给前端，表格会渲染成一格 ``-``，看起来像功能坏了。
本模块把它们统一翻译成一句人能看懂的空结果说明，交给前端提示，
提示文案会带上已解析出的时间区间，方便用户直接定位"是不是时间段选错了"。

纯函数实现，不依赖数据库与模型，便于离线单元测试。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping


def is_empty_result(result: list[dict]) -> bool:
    """判断结果是否算"无数据"：空列表，或所有单元格都为 None

    Args:
        result: 仓储层返回的行字典列表

    Returns:
        True 表示没有任何有效数据
    """

    # 一行都没有，显然无数据
    if not result:
        return True
    # 有行但每一格都是 None（典型是聚合查询落在空集上），同样视为无数据
    return all(value is None for row in result for value in row.values())


def _inclusive_end(end: str) -> str:
    """把半开区间的结束日换算成更直观的闭区间末日（end 的前一天）

    时间解析产出的是半开区间 [start, end)，end 当天并不包含在内。
    直接展示给用户容易产生"为什么少了一天"的困惑，这里转成闭区间末日。
    """

    try:
        return (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    except ValueError:
        # 结束日不是标准日期时保持原样，保证兜底提示不因格式化失败而丢失
        return end


def build_empty_notice(
    result: list[dict], time_range: Mapping[str, Any] | None
) -> str | None:
    """结果非空时返回 None；结果为空时返回一句明确的空结果提示

    Args:
        result: 仓储层返回的行字典列表
        time_range: 已解析出的时间区间（含 start/end/expr），可能为 None

    Returns:
        空结果提示文案；有数据时返回 None
    """

    if not is_empty_result(result):
        return None

    # 有时间区间时，把区间（优先用原始表达，如"上一季度"）带进提示，定位更准
    if time_range and time_range.get("start") and time_range.get("end"):
        start = time_range["start"]
        end = _inclusive_end(time_range["end"])
        expr = time_range.get("expr") or ""
        span = f"{start} 至 {end}"
        prefix = f"{expr}（{span}）" if expr else span
        return f"在 {prefix} 范围内暂无数据，请换一个时间范围或筛选条件再试试。"

    # 没有时间区间时给一句通用提示，避免暴露内部结构
    return "没有查询到符合条件的数据，请调整时间范围或筛选条件再试试。"
