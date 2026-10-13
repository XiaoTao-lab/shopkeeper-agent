"""
初始化审计日志表

用法：
    uv run python -m app.scripts.init_audit_table

在 Meta MySQL 中创建 audit_log 表（幂等：IF NOT EXISTS）。
与 docker/mysql/migration_add_audit_log.sql 内容一致，这里提供"不走 docker CLI"的便捷入口。
"""

import asyncio

from sqlalchemy import text

from app.clients.mysql_client_manager import meta_mysql_client_manager

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log
(
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    ts              DATETIME     NOT NULL,
    `role`          VARCHAR(50),
    thread_id       VARCHAR(64),
    `query`         TEXT,
    rewritten_query TEXT,
    `sql`           TEXT,
    row_count       INT,
    rls_applied     TINYINT(1),
    region_ids      VARCHAR(255),
    masked_columns  VARCHAR(255),
    elapsed_ms      INT,
    status          VARCHAR(20),
    INDEX idx_ts (ts),
    INDEX idx_role (`role`)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
"""


async def main() -> None:
    meta_mysql_client_manager.init()
    async with meta_mysql_client_manager.engine.begin() as conn:
        await conn.execute(text(_DDL))
    await meta_mysql_client_manager.close()
    print("audit_log 表已就绪")


if __name__ == "__main__":
    asyncio.run(main())
