"""结果结论生成单元测试

覆盖 build_result_summary 的各类结果形态：
单行、多行、无度量列、全数值列、空值、非数值单元格与数值格式化。
"""

from decimal import Decimal

from app.core.result_summary import build_result_summary


class TestEmptyAndInvalid:
    def test_none_returns_none(self):
        assert build_result_summary(None) is None

    def test_empty_list_returns_none(self):
        assert build_result_summary([]) is None

    def test_non_list_returns_none(self):
        assert build_result_summary({"GMV": 1}) is None

    def test_non_dict_rows_return_none(self):
        assert build_result_summary([1, 2, 3]) is None

    def test_empty_dict_row_returns_none(self):
        assert build_result_summary([{}]) is None


class TestSingleRow:
    def test_single_row_single_metric(self):
        # 107373.0 是整数值浮点，应去掉多余小数并加千分位
        assert build_result_summary([{"GMV": 107373.0}]) == "GMV 107,373"

    def test_single_row_multi_columns_joined(self):
        text = build_result_summary([{"地区": "华东", "GMV": 107373.0, "订单量": 115}])
        assert text == "地区 华东，GMV 107,373，订单量 115"

    def test_single_row_none_value_renders_dash(self):
        assert build_result_summary([{"销售额": None}]) == "销售额 -"

    def test_single_row_bool_value(self):
        assert build_result_summary([{"是否活跃": True}]) == "是否活跃 True"


class TestMultiRow:
    def test_dimension_and_measure_reports_extremes(self):
        result = [
            {"地区": "华东", "GMV": 107373.0},
            {"地区": "华北", "GMV": 60000.0},
            {"地区": "华南", "GMV": 88000.0},
        ]
        text = build_result_summary(result)
        assert text is not None
        assert "「地区」中 GMV 最高的是「华东」（107,373）" in text
        assert "最低的是「华北」（60,000）" in text
        assert "共 3 组" in text

    def test_all_equal_values_reports_uniform(self):
        result = [
            {"地区": "华东", "GMV": 100},
            {"地区": "华北", "GMV": 100},
        ]
        assert build_result_summary(result) == "共 2 组，GMV均为 100。"

    def test_no_measure_column_counts_rows(self):
        # 全为非数值列 → 没有度量，只能报行数
        result = [{"地区": "华东"}, {"地区": "华北"}]
        assert build_result_summary(result) == "共 2 行结果。"

    def test_all_numeric_no_dimension_reports_extremes(self):
        result = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        assert build_result_summary(result) == "a 最高 3，最低 1，共 2 行。"


class TestNumberFormatting:
    def test_integer_uses_thousands_separator(self):
        assert build_result_summary([{"订单量": 12345}]) == "订单量 12,345"

    def test_decimal_integral(self):
        assert build_result_summary([{"销量": Decimal("622")}]) == "销量 622"

    def test_decimal_fractional(self):
        assert build_result_summary([{"AOV": Decimal("1234.567")}]) == "AOV 1,234.567"

    def test_float_keeps_fraction(self):
        assert build_result_summary([{"AOV": 2427.473913043478}]) == "AOV 2,427.4739"
