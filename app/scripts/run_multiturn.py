"""
多轮对话端到端验证脚本

模拟同一个会话（同一个 thread_id）下的连续追问，验证：
1. 首轮问题原样透传；
2. 后续口语问题（"那上个月呢""只看华东地区"）被改写节点补全成独立问题；
3. 每一轮都能独立跑通召回 → 口径冻结 → SQL 生成 → 执行。

用法（需 Docker 里的 mysql/qdrant/es/embedding 与有效 LLM Key）：
    uv run python -m app.scripts.run_multiturn
"""

import asyncio
import sys

from app.agent.context import DataAgentContext
from app.agent.graph import graph
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.value_es_repository import ValueESRepository
from app.repositories.mysql.dw.dw_mysql_repository import DWMySQLRepository
from app.repositories.mysql.meta.meta_mysql_repository import MetaMySQLRepository
from app.repositories.qdrant.column_qdrant_repository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

# 一轮会话里的连续追问：后续几句都依赖前文才能理解
TURNS = [
    "统计2025年第一季度各地区的GMV",
    "那上个月呢",
    "只看华东地区",
]
THREAD_ID = "multiturn-demo"


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    qdrant_client_manager.init()
    embedding_client_manager.init()
    es_client_manager.init()

    config = {"configurable": {"thread_id": THREAD_ID}}

    try:
        async with (
            meta_mysql_client_manager.session_factory() as meta_session,
            dw_mysql_client_manager.session_factory() as dw_session,
        ):
            context = DataAgentContext(
                column_qdrant_repository=ColumnQdrantRepository(
                    qdrant_client_manager.client
                ),
                embedding_client=embedding_client_manager.client,
                metric_qdrant_repository=MetricQdrantRepository(
                    qdrant_client_manager.client
                ),
                value_es_repository=ValueESRepository(es_client_manager.client),
                meta_mysql_repository=MetaMySQLRepository(meta_session),
                dw_mysql_repository=DWMySQLRepository(dw_session),
            )

            for i, turn in enumerate(TURNS, 1):
                final = await graph.ainvoke(
                    input={"query": turn}, context=context, config=config
                )
                rewritten = final.get("rewritten_query")
                sql = final.get("sql")
                error = final.get("error")
                print(f"\n[第 {i} 轮] 用户：{turn}")
                print(f"        改写：{rewritten}")
                print(f"        SQL ：{sql or '(未产出)'}")
                if error:
                    print(f"        错误：{error}")
    finally:
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
        await qdrant_client_manager.close()
        await es_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
