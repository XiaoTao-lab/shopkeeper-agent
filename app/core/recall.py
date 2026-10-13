"""
多路召回的公共逻辑

三路召回（字段 / 指标 / 取值）共享同一套模式：关键词扩展 -> 逐词检索 -> 按 id 去重合并。
差别只在"如何检索"——字段和指标走向量检索（先 Embedding 再查 Qdrant），取值走全文检索。
把这段确定性的复用逻辑抽到这里：既避免三处各写一份，也方便离线单测用假检索器验证
"并发、顺序、去重"的行为，而不必启动真实服务或 mock 大模型。
"""

from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol, TypeVar

from app.core.concurrency import gather_limited

T = TypeVar("T")


class Embedder(Protocol):
    """批量向量化能力的最小接口（只声明本模块用到的方法）"""

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """把一组文本批量向量化，返回顺序与输入一致的向量列表"""
        ...


async def embed_and_search(
    keywords: list[str],
    embedding_client: Embedder,
    search: Callable[[list[float]], Awaitable[list[T]]],
    limit: int,
) -> list[list[T]]:
    """先批量向量化，再并发检索，返回"每个关键词一组命中"的结果列表。

    Args:
        keywords: 已去重的检索关键词，其顺序即检索顺序
        embedding_client: 提供 ``aembed_documents`` 的 Embedding 客户端
        search: 单向量检索函数（如 ``repo.search``），返回 awaitable
        limit: 并发度上限

    Returns:
        与 ``keywords`` 顺序一致的命中分组列表

    Raises:
        向量化或任一检索抛出的异常
    """
    if not keywords:
        return []
    # 一次性批量向量化：N 个关键词只发 1 次网络往返
    embeddings = await embedding_client.aembed_documents(keywords)
    # 并发检索，并发度由 limit 约束，避免瞬时打爆下游
    return await gather_limited(
        (search(embedding) for embedding in embeddings), limit=limit
    )


async def search_by_keywords(
    keywords: list[str],
    search: Callable[[str], Awaitable[list[T]]],
    limit: int,
) -> list[list[T]]:
    """并发按关键词做全文检索（取值召回用），返回"每个关键词一组命中"的结果列表。

    Args:
        keywords: 已去重的检索关键词，其顺序即检索顺序
        search: 单关键词检索函数（如 ``value_es_repository.search``），返回 awaitable
        limit: 并发度上限

    Returns:
        与 ``keywords`` 顺序一致的命中分组列表
    """
    if not keywords:
        return []
    return await gather_limited(
        (search(keyword) for keyword in keywords), limit=limit
    )


def dedup_items(groups: Iterable[Iterable[T]], *, attr: str = "id") -> list[T]:
    """按唯一键去重合并多组召回结果，保留首次出现的顺序。

    多个关键词可能命中同一条实体（同一字段的多个向量点、同一条取值记录等），
    这里以 ``attr`` 为唯一键合并，先到先得。

    Args:
        groups: 多组待合并的实体
        attr: 用于判重的属性名，三路实体的唯一键都叫 ``id``

    Returns:
        去重后的实体列表
    """
    merged: dict[str, T] = {}
    for group in groups:
        for item in group:
            merged.setdefault(getattr(item, attr), item)
    return list(merged.values())
