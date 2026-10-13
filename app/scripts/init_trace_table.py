"""
初始化链路追踪表

用法：
    uv run python -m app.scripts.init_trace_table

在 Meta MySQL 中创建 trace（头表）与 trace_span（明细表），幂等（IF NOT EXISTS）。
与 docker/mysql/migration_add_trace.sql 内容一致，这里提供"不走 docker CLI"的便捷入口。
"""

import asyncio

from sqlalchemy import text

from app.clients.mysql_client_manager import meta_mysql_client_manager

# 拆成两条执行：部分 MySQL 驱动默认不允许一次提交多条语句
_DDL_TRACE = """
CREATE TABLE IF NOT EXISTS `trace`
(
    id                BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id          VARCHAR(36) NOT NULL,
    thread_id         VARCHAR(64),
    request_id        VARCHAR(64),
    `query`           TEXT,
    `role`            VARCHAR(50),
    status            VARCHAR(20),
    total_ms          INT,
    span_ms           INT,
    span_count        INT,
    llm_calls         INT,
    prompt_tokens     INT,
    completion_tokens INT,
    created_at        DATETIME    NOT NULL,
    UNIQUE KEY uk_trace_id (trace_id),
    INDEX idx_created_at (created_at),
    INDEX idx_status (status),
    INDEX idx_thread (thread_id)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
"""

_DDL_TRACE_SPAN = """
CREATE TABLE IF NOT EXISTS trace_span
(
    id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    trace_id   VARCHAR(36) NOT NULL,
    seq        INT,
    node       VARCHAR(64),
    status     VARCHAR(20),
    elapsed_ms INT,
    error      VARCHAR(500),
    INDEX idx_trace (trace_id),
    INDEX idx_node (node)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
"""


async def main() -> None:
    meta_mysql_client_manager.init()
    async with meta_mysql_client_manager.engine.begin() as conn:
        await conn.execute(text(_DDL_TRACE))
        await conn.execute(text(_DDL_TRACE_SPAN))
    await meta_mysql_client_manager.close()
    print("trace / trace_span 表已就绪")


if __name__ == "__main__":
    asyncio.run(main())
