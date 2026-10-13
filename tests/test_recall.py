"""召回公共逻辑单元测试：批量向量化、并发检索分组、按 id 去重合并。"""

import asyncio
from dataclasses import dataclass

from app.core.recall import dedup_items, embed_and_search, search_by_keywords


@dataclass
class _Item:
    """模拟 ColumnInfo / MetricInfo / ValueInfo 的最小实体（唯一键叫 id）"""

    id: str
    score: int = 0


class _FakeEmbedder:
    """记录调用次数，用来验证"批量"而不是"逐条"向量化"""

    def __init__(self) -> None:
        self.batch_calls = 0
        self.seen: list[list[str]] = []

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        self.batch_calls += 1
        self.seen.append(list(texts))
        # 用文本长度编码成向量，便于在结果里回推对应关系
        return [[float(len(text))] for text in texts]


def test_embed_and_search_batches_embedding_once() -> None:
    """N 个关键词只调用一次批量向量化，且结果按输入顺序分组"""
    embedder = _FakeEmbedder()

    async def _search(embedding: list[float]) -> list[_Item]:
        return [_Item(id=f"c{int(embedding[0])}")]

    groups = asyncio.run(
        embed_and_search(["a", "bb", "ccc"], embedder, _search, limit=8)
    )
    assert embedder.batch_calls == 1
    assert embedder.seen == [["a", "bb", "ccc"]]
    assert [group[0].id for group in groups] == ["c1", "c2", "c3"]


def test_embed_and_search_empty_short_circuits() -> None:
    """空关键词不触发任何向量化或检索"""
    embedder = _FakeEmbedder()

    async def _search(_: list[float]) -> list[_Item]:
        raise AssertionError("空输入不应发起检索")

    assert asyncio.run(embed_and_search([], embedder, _search, limit=8)) == []
    assert embedder.batch_calls == 0


def test_search_by_keywords_keeps_order() -> None:
    """取值召回：按关键词并发检索，分组顺序与输入一致"""

    async def _search(keyword: str) -> list[_Item]:
        return [_Item(id=keyword)]

    groups = asyncio.run(search_by_keywords(["华北", "华南"], _search, limit=8))
    assert [group[0].id for group in groups] == ["华北", "华南"]


def test_search_by_keywords_empty_short_circuits() -> None:
    """空关键词不发起检索"""

    async def _search(_: str) -> list[_Item]:
        raise AssertionError("空输入不应发起检索")

    assert asyncio.run(search_by_keywords([], _search, limit=8)) == []


def test_dedup_items_merges_and_keeps_first() -> None:
    """同一 id 多次命中只保留首次出现的实体"""
    first = _Item(id="x", score=1)
    duplicate = _Item(id="x", score=2)
    other = _Item(id="y")
    merged = dedup_items([[first], [duplicate, other]])
    assert merged == [first, other]
    assert merged[0].score == 1


def test_dedup_items_preserves_group_order() -> None:
    """去重后的顺序跟随分组顺序"""
    merged = dedup_items([[_Item("b")], [_Item("a")]])
    assert [item.id for item in merged] == ["b", "a"]


def test_dedup_items_empty() -> None:
    """空输入返回空列表"""
    assert dedup_items([]) == []
