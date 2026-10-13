-- 审计日志表（B2 权限与审计）
-- 记录每一次问数：谁、何时、问了什么、最终执行的 SQL、返回多少行、是否做了权限过滤 / 脱敏。
-- 注意：`role` / `sql` / `query` 是 MySQL 保留字，必须用反引号。
-- 便捷建表（不走 docker CLI）：uv run python -m app.scripts.init_audit_table

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
  DEFAULT CHARSET = utf8mb4;
