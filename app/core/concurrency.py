"""
并发执行工具

问数链路里有多处"一组同类任务并发跑"的场景（当前是字段 / 指标 / 取值三路召回按关键词
并发检索）。直接 ``asyncio.gather`` 会把 N 个请求同时打给下游服务，关键词较多时可能造成
瞬时压力；这里统一提供一个带并发度上限的封装。

语义与 ``asyncio.gather`` 保持一致：任一任务抛错则整体抛错（已发出的请求不会被取消），
只是把"同时在飞"的请求数收敛到上限。
"""

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

T = TypeVar("T")


async def gather_limited(coros: Iterable[Awaitable[T]], limit: int) -> list[T]:
    """并发执行一组 awaitable，并把并发度限制在 limit 以内。

    Args:
        coros: 待执行的 awaitable 序列，例如 ``(repo.search(e) for e in embeddings)``
        limit: 允许同时在飞的最大任务数；<=0 或大于任务数时退化为普通 ``asyncio.gather``

    Returns:
        结果列表，顺序与输入一致（与 ``asyncio.gather`` 相同）

    Raises:
        第一个抛出异常的 awaitable 的异常（其余已发出的请求不会被取消）
    """
    pendings = list(coros)
    if limit <= 0 or limit >= len(pendings):
        return await asyncio.gather(*pendings)

    semaphore = asyncio.Semaphore(limit)

    async def _guarded(coro: Awaitable[T]) -> T:
        # 只有抢到信号量才真正发起请求，从而把"同时在飞"的数量压到 limit
        async with semaphore:
            return await coro

    return await asyncio.gather(*(_guarded(coro) for coro in pendings))
