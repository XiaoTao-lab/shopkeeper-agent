"""查询结果空值兜底单元测试

覆盖 is_empty_result 与 build_empty_notice 的全部判定分支。
"""

from app.core.result_guard import build_empty_notice, is_empty_result


class TestIsEmptyResult:
    def test_empty_list_is_empty(self):
        assert is_empty_result([]) is True

    def test_single_all_none_row_is_empty(self):
        # 聚合查询落在空集上的典型形态：SUM/AVG 返回 NULL
        assert is_empty_result([{"销售额": None}]) is True

    def test_multi_all_none_rows_is_empty(self):
        assert is_empty_result([{"v": None, "n": None}, {"v": None, "n": None}]) is True

    def test_row_with_value_is_not_empty(self):
        assert is_empty_result([{"GMV": 279159.5}]) is False

    def test_partial_none_is_not_empty(self):
        # 只要有一格非空就视为有数据，不能误报空
        assert is_empty_result([{"a": None, "b": 1}]) is False

    def test_zero_value_is_not_empty(self):
        # 0 是有效数据，不是 None，不能被当成空
        assert is_empty_result([{"订单量": 0}]) is False


class TestBuildEmptyNotice:
    def test_has_data_returns_none(self):
        assert build_empty_notice([{"GMV": 100}], None) is None

    def test_zero_value_returns_none(self):
        assert build_empty_notice([{"订单量": 0}], None) is None

    def test_empty_without_time_range_gives_generic(self):
        notice = build_empty_notice([], None)
        assert notice is not None
        assert "没有查询到" in notice

    def test_all_none_without_time_range_gives_generic(self):
        assert build_empty_notice([{"销售额": None}], None) is not None

    def test_empty_with_time_range_includes_expr_and_span(self):
        time_range = {
            "start": "2026-04-01",
            "end": "2026-07-01",
            "granularity": "quarter",
            "expr": "上一季度",
            "source": "rule",
        }
        notice = build_empty_notice([], time_range)
        assert notice is not None
        # 原始表达（上一季度）应出现在提示里
        assert "上一季度" in notice
        # 半开区间 end(2026-07-01) 展示时应转为闭区间末日 2026-06-30
        assert "2026-04-01 至 2026-06-30" in notice

    def test_time_range_without_expr_uses_span_only(self):
        time_range = {"start": "2026-04-01", "end": "2026-07-01"}
        notice = build_empty_notice([{"销售额": None}], time_range)
        assert notice is not None
        assert "2026-04-01 至 2026-06-30" in notice

    def test_time_range_missing_bounds_falls_back_to_generic(self):
        # 只有 expr、没有 start/end 时不能拼出错误区间，退回通用提示
        notice = build_empty_notice([], {"expr": "上一季度"})
        assert notice is not None
        assert "没有查询到" in notice

    def test_invalid_end_keeps_raw_value(self):
        # 结束日不是合法日期时不抛异常，原样保留
        notice = build_empty_notice([], {"start": "2026-04-01", "end": "not-a-date"})
        assert notice is not None
        assert "not-a-date" in notice
