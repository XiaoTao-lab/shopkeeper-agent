"""
结果图表规划（确定性，纯函数）

用户看完一张结果表，往往还想知道"这些数字长什么样"——谁高谁低、是涨是跌。
本模块用**纯规则**判断这张表该配什么图，产出一份"画图说明书"（chart spec），
交给前端照着渲染。

刻意不调用大模型：
- 选图必须与数据结构**严格一致**，LLM 判断不稳定；
- 纯函数可离线单测、零成本、零延迟。

设计原则：**能画才画，画得对才画；宁可不出图，也绝不出一张误导人的图。**
以下情况一律不出图：

- 只有一个数（n ≤ 1）—— 一个数画什么图，结论卡片足够；
- 没有维度列，或维度不唯一（多维度时 x 轴语义会混乱）；
- 没有度量列（全是文本）；
- 维度去重后只剩一个取值（已经退化成单值）；
- 行数超上限（看图不如看表）。

出图规则：

- 维度是**时间**且只有一个度量 → **折线**（看走势），并按自然序给出 x 轴顺序；
- 其余 1 个维度 + 若干度量 → **柱状**（看对比 / 排名）：
  多个度量**单位一致**时画多系列，单位不一致（如"件"与"元"）时只画第一个
  —— 单位不同的量共用一个 y 轴，小数量级那条会被压成看不见的一线，属于误导。

列分类（含"时间列也算维度"的关键例外）统一走 ``app.core.table_shape``，
与"一句话结论"同口径——保证结论和图形不会一个说 A、一个画 B。

只下发"怎么画"，**不下发数据**：前端复用结果里已有的数据，不重复传输。
"""

import re

from app.core.table_shape import find_dimensions, find_measures, is_time_column

# 折线最多画多少个点（再多眼睛跟不上，也不如直接看表）
_MAX_LINE_POINTS = 60
# 柱状最多画多少个类目
_MAX_BAR_ITEMS = 30
# 最多同时画几个度量（多则只取前 N 个，避免柱子挤成一团）
_MAX_MEASURES = 3


def natural_key(value: object) -> tuple:
    """把维度值转成可排序的键，用于把时间轴排成正确顺序。

    优先抽出数字（"2025-01" → (2025, 1)、"第3季度" → (3,)、"10月" → (10,)），
    抽不到数字则退回原字符串，保证任何取值都不会因为排序抛异常。

    注意：SQL 若没写 ORDER BY，返回的时间行可能是乱序的，直接连线会画出"锯齿"，
    所以折线必须由这里给出确定的轴序。
    """
    numbers_found = tuple(int(item) for item in re.findall(r"\d+", str(value)))
    return (0, numbers_found, str(value))


def _dedup(values: list[object]) -> list[object]:
    """按出现顺序去重（维度取值可能有重复行，去重后才是真正的类目数）"""
    unique: list[object] = []
    for value in values:
        if value is not None and value not in unique:
            unique.append(value)
    return unique


def _unit_of(measure: str, metric_infos: list[dict] | None) -> str | None:
    """尽力取度量的单位：结果列名与命中指标名一致时即可拿到"""
    for metric in metric_infos or []:
        if metric.get("name") == measure and metric.get("unit"):
            return str(metric["unit"])
    return None


def plan_chart(result: object, metric_infos: list[dict] | None = None) -> dict | None:
    """判断结果表该配什么图。

    Args:
        result: 结果集，形如 ``[{"地区": "华东", "GMV": 107373.0}, ...]``
        metric_infos: 命中的指标元信息，用于补全单位（可为空）

    Returns:
        图表说明书字典；判定"不该出图"时返回 None
    """
    if not isinstance(result, list) or not result:
        return None

    rows = [row for row in result if isinstance(row, dict)]
    if not rows:
        return None

    columns = list(rows[0].keys())
    if not columns:
        return None

    # 只有一个数 —— 结论卡片足够，画图反而多余
    if len(rows) <= 1:
        return None

    # 列分类与"一句话结论"共用 table_shape（含"时间列算维度"的处理），
    # 保证结论和图形不会一个说 A 一个画 B
    dimensions = find_dimensions(rows, columns)
    measures = find_measures(rows, columns)
    # 维度必须**恰好一个**、度量至少一个，才谈得上"对比 / 趋势"
    # （多维度时 x 轴语义会混乱，保守不出图）
    if len(dimensions) != 1 or not measures:
        return None
    dimension = dimensions[0]

    # 维度去重后再看类目数：只剩一个取值说明已经退化成单值
    labels = _dedup([row.get(dimension) for row in rows])
    if len(labels) <= 1:
        return None

    measures = measures[:_MAX_MEASURES]
    units = [_unit_of(measure, metric_infos) for measure in measures]

    # 时间维度 + 恰好一个度量 → 折线看走势（多度量画线会互相遮挡）
    if is_time_column(dimension) and len(measures) == 1 and len(rows) <= _MAX_LINE_POINTS:
        ordered = sorted(labels, key=natural_key)
        return {
            "type": "line",
            "dimension": dimension,
            "measures": measures,
            "unit": units[0],
            "order": [str(label) for label in ordered],
            "reason": f"「{dimension}」是时间维度 → 折线看走势",
        }

    # 柱状看对比。多度量共用一根 y 轴，只有**单位一致**才可比：
    # "件"和"元"放同一根轴上，小数量级那条会被压成看不见的一线，是误导。
    # 所以单位不一致（或拿不到单位）时退回只画第一个度量，并在 reason 里讲清楚。
    degraded = len(measures) > 1 and (None in units or len(set(units)) != 1)
    if degraded:
        measures = measures[:1]
        units = units[:1]

    # 行数太多：看图不如看表，保守不出图
    if len(rows) > _MAX_BAR_ITEMS:
        return None

    reason = (
        f"度量单位不一致，仅展示「{measures[0]}」→ 柱状看对比"
        if degraded
        else f"1 个维度 + {len(measures)} 个度量 → 柱状看对比"
    )
    return {
        "type": "bar",
        "dimension": dimension,
        "measures": measures,
        "unit": units[0],
        "order": [str(label) for label in labels],
        "reason": reason,
    }
