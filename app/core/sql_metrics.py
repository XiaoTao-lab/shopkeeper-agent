"""
NL2SQL 评测度量工具

提供两个纯函数，供评测脚本与单元测试复用，不依赖数据库与模型：

- :func:`normalize_sql`：把 SQL 规范化成可比形态（去输出别名 / 去表限定符 /
  去表别名 / 去分页，统一大小写），用于 EM（Exact Match）比对；
- :func:`normalize_rows`：把结果集归一化成"行内无序、行间无序"的可比较结构，
  用于 EX（Execution Accuracy）比对。

设计意图：EM 会刻意忽略与语义无关的"写法噪音"（别名、限定符、分页），
否则同一句 SQL 只因 LLM 是否加表名就判错，指标会失真。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp


def normalize_sql(sql: str) -> str:
    """把 SQL 规范化成可比形态

    忽略三处与语义无关的写法差异：
    - 输出别名（``SUM(x) AS 销售额``）；
    - 列的表限定符（``fact_order.date_id`` vs ``date_id``）；
    - 表的别名（``fact_order AS f`` vs ``fact_order``）。

    另外会去掉 LIMIT/OFFSET——安全沙箱会统一注入 ``LIMIT 1000``，
    而 gold SQL 通常没有，不忽略会误判。
    """

    expr = sqlglot.parse_one(sql, read="mysql")
    # 输出别名去掉后，SELECT 列表只剩表达式本身
    for alias in list(expr.find_all(exp.Alias)):
        alias.replace(alias.this)
    # 去掉列的表限定符，规避"加不加表名"带来的匹配噪音
    for column in list(expr.find_all(exp.Column)):
        if column.args.get("table"):
            column.set("table", None)
    # 去掉表的别名，别名只是书写习惯，不影响结构
    for table in list(expr.find_all(exp.Table)):
        if table.args.get("alias"):
            table.set("alias", None)
    # 忽略分页，避免沙箱注入的 LIMIT 造成差异
    expr.set("limit", None)
    expr.set("offset", None)
    return expr.sql(dialect="mysql", normalize=True)


def normalize_rows(rows: list[dict]) -> list[tuple[str, ...]]:
    """把结果集归一化成"行内无序、行间无序"的可比较结构

    列顺序、别名、行顺序都不应影响判定，因此把每行取值转字符串后排序，
    再对整体行排序；浮点先四舍五入到 4 位，规避聚合计算的浮点尾差。
    """

    normalized: list[tuple[str, ...]] = []
    for row in rows:
        cells: list[str] = []
        for value in row.values():
            if isinstance(value, float):
                value = round(value, 4)
            cells.append(str(value))
        normalized.append(tuple(sorted(cells)))
    return sorted(normalized)
