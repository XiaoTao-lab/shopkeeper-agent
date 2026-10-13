"""
结果结论生成（确定性，纯函数）

内部用户看到一张表后，第一反应往往是"所以呢？"。本模块用**纯规则**把结果表压成
一句话结论——单值直接给出，分组结果指出极值与组数——让用户不点开表格也能抓到重点。

刻意不调用大模型：
- 结论必须与表格数据**严格一致**，LLM 复述数字有翻车风险；
- 纯函数可离线单测、零成本、零延迟。

不臆造"占比/环比"这类需要额外语义的指标（如 AOV 不可加和，占比会算错），
只做"一定成立"的极值与计数描述。
"""

from decimal import Decimal

from app.core.table_shape import find_dimension, find_measures
from app.core.table_shape import is_number as _is_number

# 单行结论里多个指标的连接符
_PAIR_SEP = "，"


def _fmt(value: object) -> str:
    """把单元格值格式化成适合展示的文本（整数千分位、浮点去掉多余小数）"""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        rounded = round(value, 4)
        if rounded.is_integer():
            rounded = int(rounded)
        return f"{rounded:,}"
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return f"{int(value):,}"
        return f"{value:,}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def build_result_summary(result: object) -> str | None:
    """把结果集压成一句人话结论。

    Args:
        result: 结果集，形如 ``[{"地区": "华东", "GMV": 107373.0}, ...]``

    Returns:
        结论文本；无有效数据时返回 None（由调用方决定是否提示空结果）
    """
    if not isinstance(result, list) or not result:
        return None

    rows = [row for row in result if isinstance(row, dict)]
    if not rows:
        return None

    columns = list(rows[0].keys())
    if not columns:
        return None

    # 单行：直接把每个字段列出来，比让用户看一行表格更直观
    if len(rows) == 1:
        pairs = [f"{column} {_fmt(rows[0].get(column))}" for column in columns]
        return _PAIR_SEP.join(pairs)

    # 多行：找一个"维度列"（非全数值）和一个"度量列"（全数值）
    # 列分类与自动可视化（chart_planner）共用 table_shape，保证"结论"和"图"口径一致
    dimension = find_dimension(rows, columns)
    measures = find_measures(rows, columns)
    measure = measures[0] if measures else None

    if measure is None:
        return f"共 {len(rows)} 行结果。"

    numeric_rows = [row for row in rows if _is_number(row.get(measure))]
    if not numeric_rows:
        return f"共 {len(rows)} 行结果。"

    top = max(numeric_rows, key=lambda row: row[measure])
    bottom = min(numeric_rows, key=lambda row: row[measure])
    top_value, bottom_value = _fmt(top[measure]), _fmt(bottom[measure])

    if top_value == bottom_value:
        return f"共 {len(rows)} 组，{measure}均为 {top_value}。"

    if dimension:
        return (
            f"「{dimension}」中 {measure} 最高的是「{top[dimension]}」（{top_value}），"
            f"最低的是「{bottom[dimension]}」（{bottom_value}），共 {len(rows)} 组。"
        )

    return f"{measure} 最高 {top_value}，最低 {bottom_value}，共 {len(rows)} 行。"
