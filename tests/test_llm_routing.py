"""
模型路由单测（B1 · 轻任务关 thinking）

背景：当前模型默认开启思维链，实测 97% 的输出 token 花在 reasoning 上。
我们把“关键词扩展 / 表·指标过滤”这类轻任务节点切到关闭思维链的 llm_fast，
把“生成 SQL”这类需要推理的节点留在默认 llm。

这里守住三件事（全部离线，不联网）：
1. 配置里列出的轻任务节点确实走 llm_fast，generate_sql 必须留在默认 llm；
2. 关闭参数 enable_thinking=False 真的挂在 llm_fast 的请求参数上；
3. 路由读的是配置，改配置即可一键开关 / 回滚。
"""

from app.agent.llm import get_llm, llm, llm_fast
from app.conf.app_config import app_config

# B1 明确要关 thinking 的 5 个轻任务节点
LIGHT_TASKS = [
    "recall_column",
    "recall_value",
    "recall_metric",
    "filter_table",
    "filter_metric",
]


def test_configured_no_think_nodes_cover_light_tasks() -> None:
    """5 个轻任务节点都应在配置的关闭列表中。"""
    for node in LIGHT_TASKS:
        assert node in app_config.llm.no_think_nodes


def test_generate_sql_keeps_thinking() -> None:
    """SQL 生成依赖推理，绝不能被误配进关闭列表。"""
    assert "generate_sql" not in app_config.llm.no_think_nodes


def test_get_llm_returns_fast_for_light_task() -> None:
    assert get_llm("recall_value") is llm_fast


def test_get_llm_returns_default_for_other_nodes() -> None:
    assert get_llm("generate_sql") is llm


def test_get_llm_honours_runtime_config(monkeypatch) -> None:
    """路由读的是配置：临时改配置，路由随之改变（证明可一键开关 / 回滚）。"""
    monkeypatch.setattr(app_config.llm, "no_think_nodes", ["generate_sql"])
    assert get_llm("generate_sql") is llm_fast
    assert get_llm("recall_value") is llm


def test_llm_fast_carries_thinking_off_body() -> None:
    """llm_fast 必须真的把 enable_thinking=False 透传到请求参数上。"""
    assert llm_fast.bound.extra_body == {"enable_thinking": False}
    assert llm.bound.extra_body is None


def test_nodes_wired_to_expected_llm() -> None:
    """节点模块级 llm 必须按配置接对实例。"""
    from app.agent.nodes import (
        filter_metric,
        filter_table,
        generate_sql,
        recall_column,
        recall_metric,
        recall_value,
    )

    assert recall_column.llm is llm_fast
    assert recall_value.llm is llm_fast
    assert recall_metric.llm is llm_fast
    assert filter_table.llm is llm_fast
    assert filter_metric.llm is llm_fast
    assert generate_sql.llm is llm
