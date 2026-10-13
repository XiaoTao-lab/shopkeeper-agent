-- 语义层口径字段增量迁移
--
-- 适用：meta 库已存在旧版 metric_info 表（仅 id/name/description/relevant_columns/alias）
-- 目的：补齐指标标准口径字段，使 SQL 生成阶段可以冻结口径
--
-- 用法（Docker 容器内执行）：
--   docker exec -i <mysql容器名> mysql -uroot -p决定密码 meta < docker/mysql/migration_add_metric_caliber.sql
-- 或进入容器后：
--   mysql -uroot -p meta < /path/to/migration_add_metric_caliber.sql
--
-- 提示：若直接重建数据卷（docker compose down -v && up），meta.sql 已含新字段，无需本脚本

USE meta;

ALTER TABLE metric_info
    ADD COLUMN formula         VARCHAR(512) COMMENT '标准计算口径，如 SUM(fact_order.order_amount)',
    ADD COLUMN base_table      VARCHAR(64)  COMMENT '指标主表',
    ADD COLUMN time_column     VARCHAR(64)  COMMENT '默认时间字段',
    ADD COLUMN default_filters JSON         COMMENT '默认过滤条件',
    ADD COLUMN unit            VARCHAR(16)  COMMENT '指标单位';
