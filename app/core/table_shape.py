"""
结果表结构判定（确定性，纯函数）

"一句话结论"（result_summary）和"自动可视化"（chart_planner）要先回答同一个问题：
这张结果表里，哪一列是**维度**、哪些列是**度量**？

两者必须**同口径**，否则会出现"结论说 A 最高、图上却是 B"这种自相矛盾。
把判定收拢到这一处，避免两边各写一份、日后悄悄跑偏。

关键例外：**时间列哪怕全是数字，也算维度。**
SQL 里 ``MONTH(date_id) AS 月份`` 这类时间聚合返回的是整数，若只按"是否数值"一刀切，
时间轴会被误判成度量——折线画不出来，结论也会拿月份去比大小。

纯函数、无副作用，便于离线单测。
"""

import numbers

# 时间列判定关键字：中文直接包含，英文按小写包含。
# 刻意不收裸"日"与"时间"，避免"日均销售额""处理时间"这类度量被误判成时间轴。
_TIME_KEYWORDS_CN = ("年", "月", "周", "季", "日期")
_TIME_KEYWORDS_EN = ("date", "month", "quarter", "year", "week", "day", "time")


def is_number(value: object) -> bool:
    """是否为可参与数值比较的数字（排除 bool）"""
    return isinstance(value, numbers.Number) and not isinstance(value, bool)


def is_numeric_column(rows: list[dict], column: str) -> bool:
    """该列是否在**所有行**上都是数字（空表视为否）"""
    if not rows:
        return False
    return all(is_number(row.get(column)) for row in rows)


def is_time_column(column: str) -> bool:
    """列名是否表达时间（时间列即使全是数字，也仍是"轴"而不是"度量"）"""
    lowered = column.lower()
    if any(keyword in column for keyword in _TIME_KEYWORDS_CN):
        return True
    return any(keyword in lowered for keyword in _TIME_KEYWORDS_EN)


def find_dimensions(rows: list[dict], columns: list[str]) -> list[str]:
    """维度列列表

    优先取"非全数值"的列；若整张表全是数值列，再回退到"时间列"——
    因为时间聚合会返回整数，那一列在语义上仍然是时间轴。
    """
    categorical = [column for column in columns if not is_numeric_column(rows, column)]
    if categorical:
        return categorical
    return [column for column in columns if is_time_column(column)]


def find_measures(rows: list[dict], columns: list[str]) -> list[str]:
    """度量列列表 = 数值列里去掉维度列与时间列之后剩下的部分"""
    dimensions = set(find_dimensions(rows, columns))
    return [
        column
        for column in columns
        if column not in dimensions
        and is_numeric_column(rows, column)
        and not is_time_column(column)
    ]


def find_dimension(rows: list[dict], columns: list[str]) -> str | None:
    """第一个维度列（地区 / 月份 / 品类…），没有则返回 None"""
    dimensions = find_dimensions(rows, columns)
    return dimensions[0] if dimensions else None
