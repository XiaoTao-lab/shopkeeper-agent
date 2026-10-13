"""
NL2SQL 评测脚本

对评测集逐条跑完整问数链路，拿到生成 SQL 后与 gold_sql 比对，输出两项指标：

- **EM（Exact Match）**：生成 SQL 与 gold_sql 规范化（去别名 / 去表限定符 / 去 LIMIT / 统一大小写）后完全一致；
- **EX（Execution Accuracy）**：两者各自在数仓执行，结果集（行、列均无序）完全一致。

EX 是更贴近业务的指标——只要"问出来的数一样"就算对，不苛求 SQL 写法。
脚本末尾给出通过率汇总，可选把明细写成 JSON 便于留档 / 观测。

用法：
    # 全量评测（需 Docker 里的 mysql/qdrant/es/embedding 与有效 LLM Key）
    uv run python -m app.scripts.eval_nl2sql

    # 只跑前 5 条（快速冒烟）
    uv run python -m app.scripts.eval_nl2sql --limit 5

    # 只跑指定用例
    uv run python -m app.scripts.eval_nl2sql --only E-08

    # 输出明细 JSON
    uv run python -m app.scripts.eval_nl2sql --out reports/nl2sql_eval.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import yaml

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.sql_metrics import normalize_rows, normalize_sql
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

# 用例文件固定放在 tests/eval 下，避免依赖运行时工作目录
CASES_PATH = (
    Path(__file__).resolve().parents[2] / "tests" / "eval" / "nl2sql_cases.yaml"
)


def _em(gen_sql: str | None, gold_sql: str) -> bool:
    """规范化后 SQL 是否完全一致"""
    if not gen_sql:
        return False
    try:
        return normalize_sql(gen_sql) == normalize_sql(gold_sql)
    except Exception:
        return False


async def _ex(
    dw_repository: DWMySQLRepository, gen_sql: str, gold_sql: str
) -> tuple[bool, str]:
    """执行 gold 与生成 SQL，比较结果集是否一致"""
    try:
        gold_rows = await dw_repository.run(gold_sql)
    except Exception as e:  # gold 都跑不动说明用例本身有问题，暴露出来
        return False, f"gold_sql 执行失败：{e}"

    try:
        gen_rows = await dw_repository.run(gen_sql)
    except Exception as e:
        return False, f"生成SQL执行失败：{e}"

    matched = normalize_rows(gold_rows) == normalize_rows(gen_rows)
    detail = f"gold {len(gold_rows)} 行 / gen {len(gen_rows)} 行"
    return matched, detail


async def _run_one(
    case: dict, context: DataAgentContext, dw_repository: DWMySQLRepository
) -> dict:
    """跑单条用例，返回评测明细"""

    state = DataAgentState(query=case["question"])
    # ainvoke 拿到的是图执行完的最终 state，可直接读 sql / error
    # 挂了 checkpointer 后必须提供 thread_id；用用例 id 作为会话键，保证各用例历史互不串扰
    config = {"configurable": {"thread_id": f"eval-{case['id']}"}}
    final_state = await graph.ainvoke(input=state, context=context, config=config)
    gen_sql = final_state.get("sql")
    error = final_state.get("error")

    em = _em(gen_sql, case["gold_sql"])
    if error or not gen_sql:
        ex, ex_detail = False, f"链路未产出 SQL：{error}"
    else:
        ex, ex_detail = await _ex(dw_repository, gen_sql, case["gold_sql"])

    return {
        "id": case["id"],
        "question": case["question"],
        "note": case.get("note", ""),
        "gold_sql": case["gold_sql"],
        "gen_sql": gen_sql,
        "error": error,
        "em": em,
        "ex": ex,
        "ex_detail": ex_detail,
    }


async def evaluate(
    cases: list[dict],
    context: DataAgentContext,
    dw_repository: DWMySQLRepository,
    limit: int | None,
    only: str | None,
) -> list[dict]:
    """逐条评测，返回明细列表"""

    results: list[dict] = []
    for case in cases:
        if only and case["id"] != only:
            continue
        if limit is not None and len(results) >= limit:
            break
        print(f"… 评测 {case['id']}：{case['question']}")
        results.append(await _run_one(case, context, dw_repository))
    return results


def _print_report(results: list[dict]) -> None:
    """打印逐条明细与汇总"""

    print(f"\n{'=' * 90}")
    print("逐条明细")
    print(f"{'=' * 90}")
    for r in results:
        em_flag = "EM✅" if r["em"] else "EM❌"
        ex_flag = "EX✅" if r["ex"] else "EX❌"
        print(f"[{r['id']}] {em_flag} {ex_flag}  {r['question']}")
        print(f"      gold: {r['gold_sql']}")
        print(f"      gen : {r['gen_sql'] or '(无)'}")
        if not r["ex"]:
            print(f"      差异: {r['ex_detail']}")
        print()

    total = len(results)
    em_ok = sum(1 for r in results if r["em"])
    ex_ok = sum(1 for r in results if r["ex"])
    em_rate = em_ok / total * 100 if total else 0.0
    ex_rate = ex_ok / total * 100 if total else 0.0

    print(f"{'#' * 90}")
    print(f"用例总数：{total}")
    print(f"EM（规范化完全一致）：{em_ok}/{total} = {em_rate:.1f}%")
    print(f"EX（执行结果一致）  ：{ex_ok}/{total} = {ex_rate:.1f}%")
    print(f"{'#' * 90}")


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="NL2SQL 评测")
    parser.add_argument("--limit", type=int, default=None, help="只评测前 N 条")
    parser.add_argument("--only", type=str, default=None, help="只评测指定用例 id")
    parser.add_argument(
        "--cases", type=str, default=str(CASES_PATH), help="用例文件路径"
    )
    parser.add_argument("--out", type=str, default=None, help="把明细写成 JSON 文件")
    args = parser.parse_args()

    cases = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))["cases"]

    # 初始化问数链路需要的全部外部依赖
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()

    try:
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            meta_mysql_repository = MetaMySQLRepository(meta_session)
            dw_mysql_repository = DWMySQLRepository(dw_session)
            context = DataAgentContext(
                column_qdrant_repository=ColumnQdrantRepository(
                    qdrant_client_manager.client
                ),
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=MetricQdrantRepository(
                    qdrant_client_manager.client
                ),
                value_es_repository=ValueESRepository(es_client_manager.client),
                meta_mysql_repository=meta_mysql_repository,
                dw_mysql_repository=dw_mysql_repository,
            )

            results = await evaluate(
                cases, context, dw_mysql_repository, args.limit, args.only
            )
    finally:
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()

    _print_report(results)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n明细已写入：{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
