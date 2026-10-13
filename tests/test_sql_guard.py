"""
SQL 安全沙箱单元测试

覆盖规则 R1~R5：正常放行、LIMIT 加固、写操作拦截、多语句注入、
注释不误拦、危险函数、套壳写操作、大小写混淆。

测试基于数仓真实表（fact_order / dim_region / dim_customer / dim_product）。
"""

from app.core.sql_guard import guard_sql


def test_join_select_allowed() -> None:
    """正常多表聚合查询应放行"""
    sql = (
        "SELECT r.region_name, SUM(f.order_amount) AS total "
        "FROM fact_order f JOIN dim_region r ON f.region_id = r.region_id "
        "GROUP BY r.region_name"
    )
    result = guard_sql(sql)
    assert result.passed is True
    assert result.reason == ""


def test_select_inject_limit() -> None:
    """无 LIMIT 的 SELECT 应被注入 LIMIT"""
    result = guard_sql("SELECT * FROM fact_order", max_rows=1000)
    assert result.passed is True
    assert result.modified is True
    assert "LIMIT 1000" in result.sql.upper()


def test_select_keep_existing_limit() -> None:
    """已有 LIMIT 的 SELECT 不应被改写"""
    result = guard_sql("SELECT * FROM fact_order LIMIT 5")
    assert result.passed is True
    assert result.modified is False


def test_cte_select_allowed() -> None:
    """CTE（WITH ... SELECT）属于只读查询，不应误拦"""
    result = guard_sql("WITH x AS (SELECT 1 AS n) SELECT * FROM x")
    assert result.passed is True


def test_delete_rejected() -> None:
    """DELETE 应被拦截"""
    result = guard_sql("DELETE FROM fact_order WHERE order_amount < 10")
    assert result.passed is False
    assert "写操作" in result.reason


def test_update_rejected() -> None:
    """UPDATE 应被拦截"""
    result = guard_sql("UPDATE dim_product SET brand = 'X'")
    assert result.passed is False


def test_drop_rejected() -> None:
    """DROP TABLE 应被拦截"""
    result = guard_sql("DROP TABLE dim_customer")
    assert result.passed is False


def test_insert_rejected() -> None:
    """INSERT 应被拦截"""
    result = guard_sql("INSERT INTO fact_order SELECT * FROM fact_order")
    assert result.passed is False


def test_multi_statement_rejected() -> None:
    """多语句注入应被拦截"""
    result = guard_sql("SELECT * FROM fact_order; DROP TABLE dim_customer;")
    assert result.passed is False
    assert "多条" in result.reason


def test_comment_not_false_positive() -> None:
    """普通注释应被正确忽略，不误拦"""
    result = guard_sql("SELECT * FROM fact_order /* normal comment */")
    assert result.passed is True


def test_dangerous_function_rejected() -> None:
    """危险函数 SLEEP 应被拦截"""
    result = guard_sql("SELECT SLEEP(10)")
    assert result.passed is False
    assert "危险函数" in result.reason


def test_case_insensitive_rejected() -> None:
    """大小写混淆的写操作应被拦截"""
    result = guard_sql("delete from fact_order")
    assert result.passed is False


def test_empty_rejected() -> None:
    """空 SQL 应被拦截"""
    result = guard_sql("   ")
    assert result.passed is False


def test_unparsable_rejected() -> None:
    """无法解析的 SQL 应被拦截"""
    result = guard_sql("SELCT * FROM fact_order WHERE")
    assert result.passed is False
