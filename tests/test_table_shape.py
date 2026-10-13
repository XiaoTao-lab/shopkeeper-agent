"""
table_shape 单元测试

这份模块是"一句话结论"和"自动可视化"**共用**的列分类口径，边界必须锁死：
一旦分类跑偏，结论和图形会同时出错、甚至互相矛盾。

重点覆盖真实踩到的坑：**时间聚合返回整数**。
``MONTH(date_id) AS 月份`` 的结果列全是数字，但它在语义上仍是时间轴，不能被当成度量。
"""

from app.core.table_shape import (
    find_dimension,
    find_dimensions,
    find_measures,
    is_number,
    is_numeric_column,
    is_time_column,
)

_NUMERIC_ROWS = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
_REGION_ROWS = [{"地区": "华东", "GMV": 100.0}, {"地区": "华南", "GMV": 200.0}]
_NUMERIC_MONTH_ROWS = [{"月份": 1, "GMV": 100.0}, {"月份": 2, "GMV": 200.0}]


class TestIsNumber:
    def test_true_for_int_and_float(self) -> None:
        assert is_number(1) and is_number(1.5)

    def test_false_for_bool(self) -> None:
        """bool 是 int 的子类，但不应被当成数字"""
        assert not is_number(True)

    def test_false_for_text(self) -> None:
        assert not is_number("1")


class TestIsNumericColumn:
    def test_numeric_column(self) -> None:
        assert is_numeric_column(_NUMERIC_ROWS, "a")

    def test_text_column(self) -> None:
        assert not is_numeric_column(_REGION_ROWS, "地区")

    def test_mixed_column_is_not_numeric(self) -> None:
        assert not is_numeric_column([{"x": 1}, {"x": "非数"}], "x")

    def test_empty_rows_is_not_numeric(self) -> None:
        assert not is_numeric_column([], "a")


class TestIsTimeColumn:
    def test_chinese_time_words(self) -> None:
        assert is_time_column("月份")
        assert is_time_column("年份")
        assert is_time_column("季度")

    def test_english_time_words(self) -> None:
        assert is_time_column("date_id")
        assert is_time_column("order_month")

    def test_measure_named_with_day_is_not_time(self) -> None:
        """「日均销售额」是度量，不能因为带个"日"就当成时间轴"""
        assert not is_time_column("日均销售额")

    def test_plain_dimension_is_not_time(self) -> None:
        assert not is_time_column("地区")
        assert not is_time_column("region_name")


class TestFindDimensions:
    def test_prefers_text_column(self) -> None:
        assert find_dimensions(_REGION_ROWS, ["地区", "GMV"]) == ["地区"]

    def test_numeric_time_column_still_counts_as_dimension(self) -> None:
        """整数月份必须被认成维度，否则折线根本画不出来"""
        assert find_dimensions(_NUMERIC_MONTH_ROWS, ["月份", "GMV"]) == ["月份"]

    def test_all_numeric_without_time_has_no_dimension(self) -> None:
        assert find_dimensions(_NUMERIC_ROWS, ["a", "b"]) == []

    def test_multiple_text_columns_all_returned(self) -> None:
        rows = [{"地区": "华东", "省份": "上海", "GMV": 1.0}]
        assert find_dimensions(rows, ["地区", "省份", "GMV"]) == ["地区", "省份"]

    def test_find_dimension_returns_first(self) -> None:
        assert find_dimension(_REGION_ROWS, ["地区", "GMV"]) == "地区"

    def test_find_dimension_none_when_nothing(self) -> None:
        assert find_dimension(_NUMERIC_ROWS, ["a", "b"]) is None


class TestFindMeasures:
    def test_excludes_dimension(self) -> None:
        assert find_measures(_REGION_ROWS, ["地区", "GMV"]) == ["GMV"]

    def test_excludes_numeric_time_column(self) -> None:
        """整数月份是轴不是度量，不能混进度量里（否则会多画一条"月份"柱）"""
        assert find_measures(_NUMERIC_MONTH_ROWS, ["月份", "GMV"]) == ["GMV"]

    def test_multiple_measures_keep_order(self) -> None:
        rows = [{"品类": "A", "销量": 1, "销售额": 2}]
        assert find_measures(rows, ["品类", "销量", "销售额"]) == ["销量", "销售额"]

    def test_no_measure_when_all_text(self) -> None:
        assert find_measures([{"地区": "华东"}], ["地区"]) == []
