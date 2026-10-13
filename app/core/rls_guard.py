"""
行级权限注入（Row-Level Security）

把角色的**区域访问范围**硬注入到生成的 SQL 里，实现"不同的人只能看到自己有权看的数据行"。

设计要点（与 SQL 沙箱一脉相承：能确定性管的，绝不交给 LLM）：

- **内核强制**：不改提示词"求"模型自觉，而是在 SQL 上直接 AND 一个区域过滤条件，模型看不到也去不掉；
- **结构化配置**：区域用 id 白名单（如 R002/R005），不写裸 SQL 片段，避免配置注入；
- **落在事实表上**：优先 `fact_order.region_id`，退到 `dim_region.region_id`；两者都不涉及则视为不敏感、不动；
- **注入到「表所在层」**：条件加到**能看见该表的那一层 SELECT**（可能是子查询），而非无条件加到最外层——
  模型若把事实表包在子查询里（`FROM (SELECT ... FROM fact_order) AS t JOIN ...`），最外层不认识 `fact_order`，
  把条件加到最外层会直接报 `Unknown column`；
- **幂等**：若语句里已存在完全相同的区域 IN 条件，则跳过，避免在"校验失败→修正→重跑"回路里重复叠加。

本模块是纯函数、无副作用、不依赖数据库连接，便于单元测试与复用。
"""

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# 与数仓实际数据库保持一致
_DIALECT = "mysql"

# 承载区域过滤的候选表，按优先级排列：事实表在前，维表兜底
_SCOPE_TABLES = ("fact_order", "dim_region")


@dataclass
class RlsResult:
    """行级过滤注入结果。

    Attributes:
        sql: 处理后的 SQL（未注入时等于原 SQL）
        applied: 是否真正应用了行级过滤（全量角色或找不到范围表时为 False）
        region_ids: 实际生效的区域白名单
        reason: 说明（applied 为 False 时给出跳过原因）
        modified: SQL 是否被改写
    """

    sql: str
    applied: bool
    region_ids: list[str] = field(default_factory=list)
    reason: str = ""
    modified: bool = False


def _find_scope_select(
    statement: exp.Expression,
) -> tuple[exp.Select | None, str | None]:
    """定位承载区域过滤的表**及其所在的 SELECT 层**，返回 (所在 SELECT, 别名或表名)。

    优先 `fact_order`（事实表，区域过滤的枢纽），退到 `dim_region`。
    两者都不在语句里时返回 (None, None)——说明该查询不涉及区域维度。

    **为何返回「所在层」而不只返回表名**：过滤条件必须注入到「能看见这张表」的那层。
    `find_all` 是**全局递归**的（能钻进子查询找到事实表），而 WHERE 若固定加在最外层，
    两者作用域就会错位。这里用 `find_ancestor` 取表的**最近 SELECT 祖先**，把条件落在该层。
    """
    tables = list(statement.find_all(exp.Table))
    for name in _SCOPE_TABLES:
        for table in tables:
            if table.name.lower() == name:
                scope_select = table.find_ancestor(exp.Select)
                if scope_select is None:
                    # 防御性兜底：Table 理论上必在某 Select 内，真出现则跳过该表
                    continue
                # alias_or_name：有别名用别名（如 fo），没有则用表名本身
                return scope_select, table.alias_or_name
    return None, None


def _has_same_region_filter(
    statement: exp.Expression, scope_alias: str, region_ids: list[str]
) -> bool:
    """判断语句里是否已存在"同一表 region_id IN(同一集合)"的条件（幂等用）。

    只认完全等价的条件：列名 region_id、表限定一致、IN 集合与目标集合相同。
    用户自己的 `region_id = 'R004'` 不等于我们的 IN 集合，因此不会误判为已注入——
    这保证"华东经理查华北"时仍会被拦截成空，而不是漏放行。
    """
    target = set(region_ids)
    alias = scope_alias.lower()
    for node in statement.find_all(exp.In):
        column = node.this
        if not isinstance(column, exp.Column):
            continue
        if column.name.lower() != "region_id":
            continue
        # 表限定为空（未限定列）或与目标别名一致，都视为作用在同一张表上
        if column.table.lower() not in ("", alias):
            continue
        values = {
            literal.name for literal in node.expressions if isinstance(literal, exp.Literal)
        }
        if values == target:
            return True
    return False


def inject_row_filter(sql: str, region_ids: list[str] | None) -> RlsResult:
    """把区域白名单注入到 SQL 的行级过滤条件里。

    Args:
        sql: 待处理的只读 SQL
        region_ids: 允许访问的区域 id 白名单；为空/None 表示该角色不做行级限制

    Returns:
        RlsResult: 注入结果；applied 为 True 时 sql 为带区域过滤的语句
    """
    raw = (sql or "").strip()

    # 全量角色：不加任何限制
    if not region_ids:
        return RlsResult(sql=raw, applied=False, reason="该角色无行级限制")

    if not raw:
        return RlsResult(sql=raw, applied=False, reason="SQL 为空，跳过行级过滤")

    try:
        statements = [s for s in sqlglot.parse(raw, read=_DIALECT) if s is not None]
    except ParseError as e:
        # 解析失败不阻断流程（上游 guard_sql 已保证只有合法 SELECT 会走到这里），
        # 但明确标记未应用，便于审计发现"该限制却没限制"的情况
        return RlsResult(sql=raw, applied=False, reason=f"SQL 无法解析，跳过行级过滤：{e}")

    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return RlsResult(sql=raw, applied=False, reason="非单条 SELECT，跳过行级过滤")

    statement = statements[0]

    scope_select, scope_alias = _find_scope_select(statement)
    if scope_select is None or scope_alias is None:
        return RlsResult(sql=raw, applied=False, reason="查询未涉及区域维度表，跳过行级过滤")

    # 幂等：已经注入过完全相同的区域条件就不再叠加（在表所在层内判定）
    if _has_same_region_filter(scope_select, scope_alias, region_ids):
        return RlsResult(
            sql=raw,
            applied=True,
            region_ids=list(region_ids),
            reason="已存在相同区域条件，跳过重复注入",
            modified=False,
        )

    # 构造 `alias.region_id IN ('R002','R005')` 并 AND 进**表所在层**的 WHERE
    column = exp.column("region_id", table=scope_alias)
    predicate = exp.In(
        this=column, expressions=[exp.Literal.string(r) for r in region_ids]
    )
    # Select.where 默认 append=True：已有 WHERE 时用 AND 合并，没有则新建
    # 注意：注入到 scope_select（表所在层），而非最外层 statement——否则子查询场景会报 Unknown column；
    # 序列化仍从根 statement 出发，保证返回完整 SQL。
    scope_select.where(predicate, copy=False)

    return RlsResult(
        sql=statement.sql(dialect=_DIALECT),
        applied=True,
        region_ids=list(region_ids),
        reason="已注入区域行级过滤",
        modified=True,
    )
