"""并发工具 gather_limited 的单元测试：顺序、并发上限、空输入与异常传播。"""

import asyncio

import pytest

from app.core.concurrency import gather_limited


async def _echo(value: int) -> int:
    return value


def test_gather_limited_keeps_input_order() -> None:
    """结果顺序与输入一致（与 asyncio.gather 相同）"""
    results = asyncio.run(gather_limited((_echo(i) for i in range(5)), limit=2))
    assert results == [0, 1, 2, 3, 4]


def test_gather_limited_non_positive_limit_means_unlimited() -> None:
    """limit <= 0 退化为普通 gather"""
    results = asyncio.run(gather_limited((_echo(i) for i in range(3)), limit=0))
    assert results == [0, 1, 2]


def test_gather_limited_limit_not_smaller_than_tasks() -> None:
    """limit 大于任务数时等价于全量并发"""
    results = asyncio.run(gather_limited((_echo(i) for i in range(3)), limit=10))
    assert results == [0, 1, 2]


def test_gather_limited_empty_input() -> None:
    """空输入返回空列表"""
    assert asyncio.run(gather_limited([], limit=4)) == []


def test_gather_limited_enforces_concurrency_cap() -> None:
    """并发度确实被限制：观测到的峰值并发不超过 limit"""
    state = {"current": 0, "peak": 0}

    async def _probe() -> int:
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(0.02)
        state["current"] -= 1
        return 1

    asyncio.run(gather_limited((_probe() for _ in range(10)), limit=3))
    assert state["peak"] <= 3


def test_gather_limited_propagates_exception() -> None:
    """任一任务抛错则整体抛错"""

    async def _boom() -> int:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        asyncio.run(gather_limited([_boom(), _echo(1)], limit=2))
