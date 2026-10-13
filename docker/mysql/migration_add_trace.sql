-- 链路追踪表（可观测 / B1）
--
-- trace      一次问数一行：总耗时、节点数、token 成本、最终状态
-- trace_span 一次问数的每个业务节点一行：节点名、耗时、状态、错误
--
-- 与 audit_log 互补：audit_log 记「谁查了什么」（安全维度），
-- trace 记「这步花了多久 / 成没成 / 贵不贵」（性能与成本维度）。
-- 注意：`query` / `role` 是 MySQL 保留字，必须加反引号。

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
  DEFAULT CHARSET = utf8mb4;

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
  DEFAULT CHARSET = utf8mb4;
