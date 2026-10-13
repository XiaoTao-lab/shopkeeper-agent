"""NL2SQL 评测度量工具单元测试

覆盖 normalize_sql（EM 归一化）与 normalize_rows（EX 结果比对）的关键分支。
"""

from app.core.sql_metrics import normalize_rows, normalize_sql


class TestNormalizeSql:
    def test_output_alias_ignored(self):
        a = "SELECT SUM(order_amount) FROM fact_order"
        b = "SELECT SUM(order_amount) AS gmv FROM fact_order"
        assert normalize_sql(a) == normalize_sql(b)

    def test_column_qualifier_ignored(self):
        a = "SELECT SUM(order_amount) FROM fact_order WHERE date_id >= 20250101"
        b = (
            "SELECT SUM(fact_order.order_amount) FROM fact_order "
            "WHERE fact_order.date_id >= 20250101"
        )
        assert normalize_sql(a) == normalize_sql(b)

    def test_table_alias_ignored(self):
        a = "SELECT region_name, SUM(order_amount) FROM fact_order f GROUP BY region_name"
        b = "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name"
        assert normalize_sql(a) == normalize_sql(b)

    def test_injected_limit_ignored(self):
        a = "SELECT SUM(order_amount) FROM fact_order"
        b = "SELECT SUM(order_amount) FROM fact_order LIMIT 1000"
        assert normalize_sql(a) == normalize_sql(b)

    def test_case_and_whitespace_ignored(self):
        a = "select SUM(order_amount) from fact_order where date_id >= 20250101"
        b = "SELECT   SUM( order_amount )\n  FROM fact_order  WHERE date_id >= 20250101"
        assert normalize_sql(a) == normalize_sql(b)

    def test_semantically_different_sql_differs(self):
        a = "SELECT SUM(order_amount) FROM fact_order"
        b = "SELECT AVG(order_amount) FROM fact_order"
        assert normalize_sql(a) != normalize_sql(b)

    def test_different_filter_differs(self):
        a = "SELECT SUM(order_amount) FROM fact_order WHERE date_id >= 20250101"
        b = "SELECT SUM(order_amount) FROM fact_order WHERE date_id >= 20250201"
        assert normalize_sql(a) != normalize_sql(b)


class TestNormalizeRows:
    def test_row_order_ignored(self):
        a = [{"region": "华东", "gmv": 100}, {"region": "华北", "gmv": 200}]
        b = [{"region": "华北", "gmv": 200}, {"region": "华东", "gmv": 100}]
        assert normalize_rows(a) == normalize_rows(b)

    def test_column_order_ignored(self):
        a = [{"region": "华东", "gmv": 100}]
        b = [{"gmv": 100, "region": "华东"}]
        assert normalize_rows(a) == normalize_rows(b)

    def test_different_values_differ(self):
        a = [{"gmv": 100}]
        b = [{"gmv": 101}]
        assert normalize_rows(a) != normalize_rows(b)

    def test_float_tail_diff_ignored(self):
        # 聚合计算的浮点尾差不应影响判定
        a = [{"gmv": 279159.50000001}]
        b = [{"gmv": 279159.5}]
        assert normalize_rows(a) == normalize_rows(b)

    def test_none_and_zero_distinct(self):
        a = [{"v": None}]
        b = [{"v": 0}]
        assert normalize_rows(a) != normalize_rows(b)

    def test_empty_both_sides(self):
        assert normalize_rows([]) == normalize_rows([])
