"""
chart_planner 单元测试

覆盖"自动可视化"的选图规则：该出什么图、什么时候坚决不出图。
选图必须与数据结构严格一致，所以边界情况（单值 / 无维度 / 无度量 / 单类目 / 超行数）
都要锁死，避免"画出一张误导人的图"。
"""

from app.core.chart_planner import natural_key, plan_chart

# 各地区 GMV：1 维度 1 度量，非时间 → 柱状
_REGION_ROWS = [
    {"地区": "华东", "GMV": 107373.0},
    {"地区": "华南", "GMV": 88211.0},
    {"地区": "华北", "GMV": 45230.0},
]

# 各月 GMV：时间维度 → 折线（故意乱序，用来验证轴序由 planner 纠正）
_MONTH_ROWS = [
    {"月份": "10月", "GMV": 300.0},
    {"月份": "2月", "GMV": 100.0},
    {"月份": "1月", "GMV": 200.0},
]


def test_non_list_input_returns_none() -> None:
    """非列表输入（异常）→ 不出图，避免上游崩溃"""
    assert plan_chart(None) is None


def test_empty_list_returns_none() -> None:
    assert plan_chart([]) is None


def test_single_row_returns_none() -> None:
    """只有一个数 → 不出图（结论卡片足够）"""
    assert plan_chart([{"地区": "华东", "GMV": 1.0}]) is None


def test_no_dimension_returns_none() -> None:
    """全是数值列 → 没有维度，不出图"""
    assert plan_chart([{"GMV": 1.0}, {"GMV": 2.0}]) is None


def test_no_measure_returns_none() -> None:
    """全是文本列 → 没有度量，不出图"""
    assert plan_chart([{"地区": "华东"}, {"地区": "华南"}]) is None


def test_single_category_returns_none() -> None:
    """维度去重后只剩一个取值 → 已退化成单值，不出图"""
    assert plan_chart([{"地区": "华东", "GMV": 1.0}, {"地区": "华东", "GMV": 2.0}]) is None


def test_non_time_single_measure_gives_bar() -> None:
    """1 维度 1 度量（非时间）→ 柱状"""
    chart = plan_chart(_REGION_ROWS)
    assert chart is not None
    assert chart["type"] == "bar"
    assert chart["dimension"] == "地区"
    assert chart["measures"] == ["GMV"]
    assert chart["order"] == ["华东", "华南", "华北"]


def test_time_single_measure_gives_line() -> None:
    """维度是时间 + 单个度量 → 折线"""
    chart = plan_chart(_MONTH_ROWS)
    assert chart is not None
    assert chart["type"] == "line"
    assert chart["dimension"] == "月份"


def test_line_order_is_natural_not_lexicographic() -> None:
    """轴序按数字排（1月 → 2月 → 10月），而不是字符串序（会把 10月 排到最前）"""
    chart = plan_chart(_MONTH_ROWS)
    assert chart is not None
    assert chart["order"] == ["1月", "2月", "10月"]


def test_time_dimension_with_multiple_measures_gives_bar() -> None:
    """时间 + 多度量 → 降级柱状（折线只画单度量，多条线会互相遮挡）"""
    chart = plan_chart(
        [
            {"月份": "1月", "销量": 1, "销售额": 2},
            {"月份": "2月", "销量": 3, "销售额": 4},
        ]
    )
    assert chart is not None
    assert chart["type"] == "bar"
    # 单位未知 → 再降级为单度量，避免"件"与"元"同轴不可比
    assert chart["measures"] == ["销量"]


def test_multiple_measures_are_truncated_to_three() -> None:
    """度量超过 3 个只取前 3 个；同单位时保留多系列"""
    rows = [
        {"品类": "A", "m1": 1, "m2": 2, "m3": 3, "m4": 4},
        {"品类": "B", "m1": 5, "m2": 6, "m3": 7, "m4": 8},
    ]
    metric_infos = [{"name": name, "unit": "元"} for name in ("m1", "m2", "m3", "m4")]
    chart = plan_chart(rows, metric_infos)
    assert chart is not None
    assert chart["measures"] == ["m1", "m2", "m3"]


def test_same_unit_measures_keep_multiple_series() -> None:
    """单位一致的多度量 → 保留多系列柱状"""
    rows = [
        {"品类": "A", "GMV": 1.0, "退款额": 2.0},
        {"品类": "B", "GMV": 3.0, "退款额": 4.0},
    ]
    metric_infos = [{"name": "GMV", "unit": "元"}, {"name": "退款额", "unit": "元"}]
    chart = plan_chart(rows, metric_infos)
    assert chart is not None
    assert chart["measures"] == ["GMV", "退款额"]


def test_different_unit_measures_fall_back_to_single_series() -> None:
    """「件」与「元」同轴会把小数量级压成一线 → 退回只画第一个度量"""
    rows = [
        {"品类": "A", "销量": 10, "销售额": 62190.0},
        {"品类": "B", "销量": 139, "销售额": 1115.0},
    ]
    metric_infos = [{"name": "销量", "unit": "件"}, {"name": "销售额", "unit": "元"}]
    chart = plan_chart(rows, metric_infos)
    assert chart is not None
    assert chart["measures"] == ["销量"]
    assert "销量" in chart["reason"]


def test_unknown_unit_measures_fall_back_to_single_series() -> None:
    """拿不到单位时同样保守，不硬凑多系列"""
    rows = [{"品类": "A", "销量": 1, "销售额": 2}, {"品类": "B", "销量": 3, "销售额": 4}]
    chart = plan_chart(rows)
    assert chart is not None
    assert chart["measures"] == ["销量"]


def test_too_many_rows_returns_none() -> None:
    """31 行（超柱状上限 30）且非时间维度 → 不出图"""
    rows = [{"品类": f"P{i}", "GMV": float(i)} for i in range(31)]
    assert plan_chart(rows) is None


def test_line_accepts_more_points_than_bar() -> None:
    """时间维度放宽到 60 点：40 行依然画折线"""
    rows = [{"月份": f"{i}月", "GMV": float(i)} for i in range(1, 41)]
    chart = plan_chart(rows)
    assert chart is not None
    assert chart["type"] == "line"
    assert len(chart["order"]) == 40


def test_unit_resolved_from_metric_infos() -> None:
    """列名与指标名一致时，单位从指标元信息补全"""
    chart = plan_chart(_REGION_ROWS, [{"name": "GMV", "unit": "元"}])
    assert chart is not None
    assert chart["unit"] == "元"


def test_unit_none_when_metric_not_matched() -> None:
    """指标没命中同名度量 → 不硬凑单位"""
    chart = plan_chart(_REGION_ROWS, [{"name": "订单量", "unit": "笔"}])
    assert chart is not None
    assert chart["unit"] is None


def test_spec_does_not_carry_data() -> None:
    """spec 只描述"怎么画"，不带任何数据值（数据仍走 result 事件）"""
    chart = plan_chart(_REGION_ROWS)
    assert chart is not None
    assert set(chart) == {"type", "dimension", "measures", "unit", "order", "reason"}


def test_reason_is_present() -> None:
    """带上"为什么画这个图"，供可解释面板展示"""
    chart = plan_chart(_REGION_ROWS)
    assert chart is not None
    assert chart["reason"]


def test_natural_key_extracts_numbers() -> None:
    assert natural_key("2025-03") < natural_key("2025-10")
    assert natural_key("2月") < natural_key("10月")


def test_natural_key_never_raises_on_plain_text() -> None:
    """非数字文本也能参与排序，不抛异常"""
    assert sorted(["华东", "华南", "华北"], key=natural_key)


def test_numeric_month_column_gives_line() -> None:
    """真实踩到的坑：MONTH(...) AS 月份 返回整数，时间轴不能被当成度量"""
    chart = plan_chart([{"月份": 1, "GMV": 100.0}, {"月份": 2, "GMV": 200.0}])
    assert chart is not None
    assert chart["type"] == "line"
    assert chart["dimension"] == "月份"
    assert chart["measures"] == ["GMV"]


def test_numeric_month_order_is_sorted() -> None:
    """整数月份同样要按自然序排好轴序"""
    chart = plan_chart(
        [{"月份": 3, "GMV": 1.0}, {"月份": 1, "GMV": 2.0}, {"月份": 2, "GMV": 3.0}]
    )
    assert chart is not None
    assert chart["order"] == ["1", "2", "3"]


def test_numeric_year_column_gives_line() -> None:
    chart = plan_chart([{"年份": 2024, "GMV": 1.0}, {"年份": 2025, "GMV": 2.0}])
    assert chart is not None
    assert chart["type"] == "line"


def test_numeric_non_time_dimension_is_not_charted() -> None:
    """「会员等级」是整数但不是时间 → 分不清是轴还是度量，保守不出图"""
    assert plan_chart([{"会员等级": 1, "GMV": 1.0}, {"会员等级": 2, "GMV": 2.0}]) is None


def test_two_text_dimensions_return_none() -> None:
    """多维度：x 轴语义会混乱，保守不出图"""
    rows = [
        {"地区": "华东", "品类": "A", "GMV": 1.0},
        {"地区": "华南", "品类": "B", "GMV": 2.0},
    ]
    assert plan_chart(rows) is None


def test_text_dimension_wins_over_numeric_month() -> None:
    """有文本维度时以文本列为轴；整数月份被排除出度量，不会多出一条"月份"系列"""
    rows = [
        {"地区": "华东", "月份": 1, "GMV": 1.0},
        {"地区": "华南", "月份": 2, "GMV": 2.0},
    ]
    chart = plan_chart(rows)
    assert chart is not None
    assert chart["dimension"] == "地区"
    assert chart["measures"] == ["GMV"]
