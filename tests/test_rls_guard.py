"""
行级权限注入（RLS）单元测试

覆盖 inject_row_filter 的核心行为：
全量角色放行、事实表注入、别名 JOIN、用户自带区域条件不误判、仅维表、
不涉及区域表跳过、幂等不重复叠加、解析失败/非 SELECT 跳过、
以及**子查询派生表**（关键回归：条件须注入「表所在层」而非最外层，
否则模型写成 `FROM (SELECT ... FROM fact_order) AS t` 时会报 Unknown column）。
"""

import sqlglot
from sqlglot import exp

from app.core.rls_guard import inject_row_filter

_REGIONS = ["R002", "R005"]


def _where_text(sql: str) -> str:
    """归一化取 WHERE 子句文本，便于断言"""
    statement = sqlglot.parse_one(sql, read="mysql")
    where = statement.args.get("where")
    return where.sql(dialect="mysql") if where is not None else ""


def test_full_role_no_op() -> None:
    """全量角色（无区域白名单）应原样放行，不注入"""
    sql = "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name"
    result = inject_row_filter(sql, None)
    assert result.applied is False
    assert result.modified is False
    assert result.sql == sql


def test_empty_region_list_no_op() -> None:
    """空区域列表同样不注入"""
    result = inject_row_filter("SELECT * FROM fact_order", [])
    assert result.applied is False
    assert result.modified is False


def test_inject_on_fact_order_no_alias() -> None:
    """无别名的事实表查询：注入 region_id IN (...)"""
    result = inject_row_filter(
        "SELECT region_name, SUM(order_amount) FROM fact_order GROUP BY region_name",
        _REGIONS,
    )
    assert result.applied is True
    assert result.modified is True
    assert set(result.region_ids) == set(_REGIONS)
    where = _where_text(result.sql)
    assert "region_id" in where
    assert "'R002'" in where or "R002" in where
    assert "R005" in where


def test_inject_uses_alias_in_join() -> None:
    """带别名的 JOIN：过滤应落在别名上，不能写错表名"""
    sql = (
        "SELECT r.region_name, SUM(f.order_amount) AS total "
        "FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id "
        "GROUP BY r.region_name"
    )
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    where = _where_text(result.sql)
    assert "f.region_id" in where


def test_user_self_region_still_blocked() -> None:
    """用户自带 region_id = 'R004' 不等于我们的 IN 集合，仍应注入以拦截越权"""
    sql = "SELECT SUM(order_amount) FROM fact_order WHERE region_id = 'R004'"
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    assert result.modified is True
    where = _where_text(result.sql)
    # 原有等值条件与新增 IN 条件应同时存在（用 AND 合并）
    assert "R004" in where
    assert "R002" in where
    assert "R005" in where


def test_only_dim_region_table() -> None:
    """仅涉及 dim_region（无事实表）时，退到维表注入"""
    sql = "SELECT region_name FROM dim_region"
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    assert result.modified is True
    where = _where_text(result.sql)
    assert "region_id" in where


def test_no_region_table_skip() -> None:
    """既不涉及 fact_order 也不涉及 dim_region → 视为不敏感，跳过"""
    sql = "SELECT product_name FROM dim_product"
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is False
    assert result.modified is False
    assert result.sql == sql


def test_idempotent_double_inject() -> None:
    """重复注入同一集合：第二次应识别为已存在，不再叠加"""
    first = inject_row_filter("SELECT SUM(order_amount) FROM fact_order", _REGIONS)
    assert first.modified is True

    second = inject_row_filter(first.sql, _REGIONS)
    assert second.applied is True
    assert second.modified is False
    assert second.sql == first.sql


def test_parse_error_skip() -> None:
    """无法解析的 SQL 不阻断流程，标记未应用"""
    result = inject_row_filter("SELCT * FROM", _REGIONS)
    assert result.applied is False
    assert result.modified is False


def test_empty_sql_skip() -> None:
    """空 SQL 直接跳过"""
    result = inject_row_filter("   ", _REGIONS)
    assert result.applied is False
    assert result.modified is False


def test_non_select_skip() -> None:
    """非 SELECT（理论上已被上游沙箱拦下）应跳过"""
    result = inject_row_filter("SHOW TABLES", _REGIONS)
    assert result.applied is False


def test_injected_sql_still_select() -> None:
    """注入后语句仍是合法单条 SELECT"""
    result = inject_row_filter("SELECT * FROM fact_order", _REGIONS)
    statements = sqlglot.parse(result.sql, read="mysql")
    assert len(statements) == 1
    assert isinstance(statements[0], exp.Select)


def test_region_filter_combined_with_existing_where() -> None:
    """已有 WHERE 时用 AND 合并，不覆盖原条件"""
    sql = "SELECT SUM(order_amount) FROM fact_order WHERE order_amount > 100"
    result = inject_row_filter(sql, _REGIONS)
    where = _where_text(result.sql)
    assert "order_amount > 100" in where
    assert "region_id" in where


# ---------------------------------------------------------------------------
# 子查询派生表：条件必须注入「表所在层」，而非最外层
# （原实现在此形态下会把 fact_order.region_id 加到最外层 → Unknown column）
# ---------------------------------------------------------------------------


def _subquery_where_text(sql: str) -> str:
    """取第一个派生表内层 SELECT 的 WHERE 文本"""
    statement = sqlglot.parse_one(sql, read="mysql")
    subquery = statement.find(exp.Subquery)
    assert subquery is not None, "期望 SQL 中存在子查询"
    inner = subquery.this
    assert isinstance(inner, exp.Select), "子查询体应为 SELECT"
    where = inner.args.get("where")
    return where.sql(dialect="mysql") if where is not None else ""


def _scope_where_of_fact_order(sql: str) -> str:
    """取「事实表所在那层 SELECT」的 WHERE 文本（用于嵌套场景断言）"""
    statement = sqlglot.parse_one(sql, read="mysql")
    for table in statement.find_all(exp.Table):
        if table.name.lower() == "fact_order":
            scope = table.find_ancestor(exp.Select)
            if scope is not None:
                where = scope.args.get("where")
                return where.sql(dialect="mysql") if where is not None else ""
    return ""


def test_inject_into_subquery_scope() -> None:
    """子查询派生表：条件注入子查询层，最外层不得出现 fact_order.region_id

    这就是原实现的 bug：模型写成 `FROM (SELECT ... FROM fact_order) AS t JOIN ...` 时，
    把 `fact_order.region_id` 加到最外层会报 `Unknown column 'fact_order.region_id'`。
    """
    sql = (
        "SELECT dr.region_name AS region, t.gmv FROM "
        "(SELECT region_id, SUM(order_amount) AS gmv FROM fact_order "
        "WHERE date_id >= 20250101 GROUP BY region_id) AS t "
        "INNER JOIN dim_region AS dr ON t.region_id = dr.region_id "
        "ORDER BY gmv DESC"
    )
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    assert result.modified is True

    # 最外层不得出现 fact_order.region_id（最外层看不见 fact_order）
    outer_where = _where_text(result.sql)
    assert "fact_order.region_id" not in outer_where

    # 条件落在子查询里，且不覆盖原有的 date_id 条件
    inner_where = _subquery_where_text(result.sql)
    assert "region_id" in inner_where
    assert "R002" in inner_where
    assert "R005" in inner_where
    assert "date_id" in inner_where


def test_inject_into_subquery_uses_inner_alias() -> None:
    """子查询内事实表带别名时，注入须用该别名（fo），不能写成表名"""
    sql = (
        "SELECT dr.region_name FROM "
        "(SELECT fo.region_id, SUM(fo.order_amount) AS gmv FROM fact_order AS fo "
        "GROUP BY fo.region_id) AS t "
        "JOIN dim_region AS dr ON t.region_id = dr.region_id"
    )
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    assert "fo.region_id" in _subquery_where_text(result.sql)


def test_inject_subquery_nested_two_levels() -> None:
    """两层嵌套子查询：条件应落在事实表**最近**的那层 SELECT"""
    sql = (
        "SELECT t2.gmv FROM "
        "(SELECT t1.gmv FROM "
        "(SELECT SUM(order_amount) AS gmv FROM fact_order) AS t1) AS t2"
    )
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    # 最外层仍是干净的
    assert "fact_order.region_id" not in _where_text(result.sql)
    # 事实表所在层带上了条件
    scope_where = _scope_where_of_fact_order(result.sql)
    assert "region_id" in scope_where


def test_inject_stays_outer_when_table_directly_visible() -> None:
    """事实表直接在最外层时，条件仍注入最外层（保持原行为，不过度下沉）"""
    sql = (
        "SELECT r.region_name, SUM(f.order_amount) AS total "
        "FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id "
        "GROUP BY r.region_name"
    )
    result = inject_row_filter(sql, _REGIONS)
    assert result.applied is True
    assert "f.region_id" in _where_text(result.sql)


def test_subquery_injected_sql_still_valid_select() -> None:
    """子查询场景注入后仍是合法单条 SELECT"""
    sql = "SELECT t.gmv FROM (SELECT SUM(order_amount) AS gmv FROM fact_order) AS t"
    result = inject_row_filter(sql, _REGIONS)
    statements = sqlglot.parse(result.sql, read="mysql")
    assert len(statements) == 1
    assert isinstance(statements[0], exp.Select)


def test_subquery_idempotent_double_inject() -> None:
    """子查询场景同样幂等：重复注入不再叠加"""
    sql = (
        "SELECT t.gmv FROM "
        "(SELECT region_id, SUM(order_amount) AS gmv FROM fact_order GROUP BY region_id) AS t"
    )
    first = inject_row_filter(sql, _REGIONS)
    assert first.modified is True
    second = inject_row_filter(first.sql, _REGIONS)
    assert second.applied is True
    assert second.modified is False
    assert second.sql == first.sql
