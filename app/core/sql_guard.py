"""
SQL 安全沙箱

对 LLM 生成的 SQL 做只读强制校验与加固，把"依赖 Prompt 约束"的软防护
升级为"内核层强制拦截"的硬防护。核心规则：

R1 语句白名单  只放行只读查询语句（SELECT/UNION/SHOW/DESC/EXPLAIN）
R2 写操作黑名单 显式拦截 INSERT/UPDATE/DELETE/DROP 等，给出明确原因
R3 多语句检测  拒绝 "SELECT ...; DROP ..." 这类多语句注入
R4 强制 LIMIT  对无 LIMIT 的 SELECT 注入上限，防止全表扫描拖垮数据库
R5 危险函数检测 拦截 SLEEP/BENCHMARK/LOAD_FILE 与 INTO OUTFILE

本模块是纯函数、无副作用，不依赖数据库连接，便于单元测试与复用。
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# 统一按 MySQL 方言解析，与数仓实际使用的数据库保持一致
_DIALECT = "mysql"


def _exp_types(*names: str) -> tuple[type[exp.Expression], ...]:
    """按名称安全加载 sqlglot 表达式类型，兼容不同版本的类名差异。

    Args:
        *names: 表达式类名，例如 "Select"、"TruncateTable"

    Returns:
        实际存在的表达式类型元组，不存在的名称会被忽略
    """
    return tuple(getattr(exp, name) for name in names if hasattr(exp, name))


# 允许执行的语句类型（只读白名单）
_ALLOWED_STATEMENTS = _exp_types("Select", "Union", "Show", "Describe", "Explain")

# 明确禁止的写操作 / DDL 语句类型（白名单之外的显式兜底，用于给出明确原因）
_FORBIDDEN_STATEMENTS = _exp_types(
    "Insert",
    "Update",
    "Delete",
    "Drop",
    "Create",
    "Alter",
    "TruncateTable",
    "Grant",
    "Merge",
)

# 危险函数名（大写）——延时攻击与服务器文件读写
_DANGEROUS_FUNCTIONS = frozenset(
    {"SLEEP", "BENCHMARK", "LOAD_FILE", "SYS_EXEC", "SYS_EVAL"}
)


@dataclass
class GuardResult:
    """SQL 安全校验结果。

    Attributes:
        passed: 是否放行
        sql: 放行时返回加固后的 SQL；拦截时返回原始 SQL
        reason: 拦截原因，passed 为 True 时为空串
        modified: SQL 是否被改写（例如注入了 LIMIT）
    """

    passed: bool
    sql: str
    reason: str = ""
    modified: bool = False


def _collect_function_names(node: exp.Expression) -> set[str]:
    """收集表达式中出现的所有函数名（大写），用于危险函数检测。

    Args:
        node: 已解析的 SQL 表达式

    Returns:
        函数名集合，全部转为大写
    """
    names: set[str] = set()
    for func in node.find_all(exp.Func):
        # Anonymous 承载 sqlglot 未内置的函数，名称存放在 this 字段
        if isinstance(func, exp.Anonymous):
            name = str(func.this)
        else:
            name = func.sql_name()
        names.add(name.upper())
    return names


def guard_sql(sql: str, max_rows: int = 1000) -> GuardResult:
    """对 SQL 做只读校验与加固。

    Args:
        sql: 待校验的 SQL 文本
        max_rows: 无 LIMIT 的 SELECT 自动注入的最大返回行数

    Returns:
        GuardResult: 校验结果；passed 为 True 时 sql 为加固后的语句
    """
    raw = (sql or "").strip()
    if not raw:
        return GuardResult(passed=False, sql=raw, reason="SQL 为空，拒绝执行")

    # R3 多语句检测：解析失败或语句数大于 1 都直接拦截
    # 无法解析的 SQL 不应带着未知风险进入数据库，一律拒绝
    try:
        statements = [s for s in sqlglot.parse(raw, read=_DIALECT) if s is not None]
    except ParseError as e:
        return GuardResult(passed=False, sql=raw, reason=f"SQL 无法解析：{e}")

    if not statements:
        return GuardResult(passed=False, sql=raw, reason="未解析到有效 SQL 语句")
    if len(statements) > 1:
        return GuardResult(
            passed=False, sql=raw, reason="检测到多条 SQL 语句，拒绝执行"
        )

    statement = statements[0]

    # R2 写操作黑名单：优先拦截，给出明确原因
    if isinstance(statement, _FORBIDDEN_STATEMENTS):
        return GuardResult(
            passed=False,
            sql=raw,
            reason=f"禁止执行写操作语句：{type(statement).__name__}",
        )

    # R1 语句白名单：只允许只读查询
    if not isinstance(statement, _ALLOWED_STATEMENTS):
        return GuardResult(
            passed=False,
            sql=raw,
            reason=f"仅支持查询语句，拒绝执行：{type(statement).__name__}",
        )

    # R5 补充：INTO OUTFILE / DUMPFILE 会把数据写出到服务器文件，必须拦截
    if statement.find(exp.Into):
        return GuardResult(
            passed=False, sql=raw, reason="禁止 INTO OUTFILE/DUMPFILE 导出"
        )

    # R5 危险函数检测
    hit = _collect_function_names(statement) & _DANGEROUS_FUNCTIONS
    if hit:
        return GuardResult(
            passed=False,
            sql=raw,
            reason=f"检测到危险函数：{', '.join(sorted(hit))}",
        )

    # R4 强制 LIMIT：仅对顶层 SELECT 且未显式带 LIMIT 时注入
    # 注：对聚合报表也会追加 LIMIT，业务上问数结果行数远小于上限，不影响正确性
    modified = False
    if isinstance(statement, exp.Select) and statement.args.get("limit") is None:
        statement = statement.limit(max_rows)
        modified = True

    return GuardResult(
        passed=True, sql=statement.sql(dialect=_DIALECT), modified=modified
    )
