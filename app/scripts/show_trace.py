"""
链路追踪下钻：看一次问数的"工序单"

用法：
    # 最近 5 条问数（列表）
    uv run python -m app.scripts.show_trace

    # 最近 N 条
    uv run python -m app.scripts.show_trace --last 20

    # 指定某条 trace 的完整工序单
    uv run python -m app.scripts.show_trace --trace-id 4f9a...

    # 指定会话（多轮）最近一条
    uv run python -m app.scripts.show_trace --thread-id 8f3c...

与 report_obs.py 的分工：本脚本**下钻单次**（这一次卡在哪一步），
report_obs 做**横向聚合**（P50/P95、节点耗时排行、失败归因）。
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.core.tracing import format_ms, pad_display

_SELECT_RECENT = text(
    """
    SELECT trace_id, thread_id, `query`, `role`, status,
           total_ms, span_count, llm_calls, prompt_tokens, completion_tokens, created_at
    FROM `trace`
    ORDER BY id DESC
    LIMIT :limit
    """
)

_SELECT_BY_TRACE_ID = text(
    """
    SELECT trace_id, thread_id, `query`, `role`, status,
           total_ms, span_count, llm_calls, prompt_tokens, completion_tokens, created_at
    FROM `trace`
    WHERE trace_id = :trace_id OR trace_id LIKE :trace_prefix
    ORDER BY id DESC
    LIMIT 1
    """
)

_SELECT_BY_THREAD_ID = text(
    """
    SELECT trace_id, thread_id, `query`, `role`, status,
           total_ms, span_count, llm_calls, prompt_tokens, completion_tokens, created_at
    FROM `trace`
    WHERE thread_id = :thread_id
    ORDER BY id DESC
    LIMIT 1
    """
)

_SELECT_SPANS = text(
    """
    SELECT seq, node, status, elapsed_ms, error
    FROM trace_span
    WHERE trace_id = :trace_id
    ORDER BY seq
    """
)

_STATUS_LABEL = {
    "success": "成功",
    "blocked": "已拦截",
    "error": "故障",
    "clarify": "澄清反问",
}


def _short(text_value: str | None, limit: int = 46) -> str:
    """把可能很长的查询语句压成一行短文本"""
    if not text_value:
        return "-"
    flat = " ".join(str(text_value).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _short_id(trace_id: str | None) -> str:
    """trace_id 的 8 位短前缀（列表展示用；下钻时 --trace-id 支持前缀匹配）"""
    return (trace_id or "-")[:8]


def _print_list(rows: list) -> None:
    """打印最近若干条 trace 的列表"""
    print(f"{'=' * 104}")
    print(f"最近 {len(rows)} 次问数")
    print(f"{'=' * 104}")
    print(
        pad_display("时间", 20)
        + pad_display("trace_id", 10)
        + pad_display("状态", 12)
        + pad_display("总耗时", 10, "right")
        + pad_display("节点", 6, "right")
        + pad_display("LLM", 6, "right")
        + "  问数"
    )
    print(f"{'-' * 104}")
    for row in rows:
        print(
            pad_display(row.created_at, 20)
            + pad_display(_short_id(row.trace_id), 10)
            + pad_display(row.status, 12)
            + pad_display(format_ms(row.total_ms), 10, "right")
            + pad_display(row.span_count or 0, 6, "right")
            + pad_display(row.llm_calls or 0, 6, "right")
            + f"  {_short(row.query)}"
        )
    print()
    print("提示：--trace-id 支持前缀，直接复制上面的 8 位短 id 即可下钻完整工序单")


def _print_detail(header, spans: list) -> None:
    """打印一条 trace 的完整工序单"""
    print(f"{'=' * 96}")
    print(f"trace {header.trace_id}")
    print(f"{'=' * 96}")
    print(f"问题    : {header.query}")
    print(f"角色    : {header.role}    会话: {header.thread_id}")
    print(
        f"状态    : {_STATUS_LABEL.get(header.status, header.status)}    "
        f"总耗时: {format_ms(header.total_ms)}    "
        f"LLM: {header.llm_calls or 0} 次 / "
        f"{(header.prompt_tokens or 0) + (header.completion_tokens or 0):,} tokens"
        f"（in {header.prompt_tokens or 0:,} / out {header.completion_tokens or 0:,}）"
    )
    print(f"时间    : {header.created_at}")
    print(f"{'-' * 96}")
    print(
        pad_display("#", 3, "right")
        + "  "
        + pad_display("节点", 22)
        + pad_display("状态", 10)
        + pad_display("耗时", 10, "right")
        + "   "
        + pad_display("占比", 7, "right")
    )
    print(f"{'-' * 96}")

    span_ms = sum(span.elapsed_ms for span in spans)
    for span in spans:
        # 占比以"节点耗时之和"为分母（三路召回等分支是并行执行的，
        # 用墙钟总耗时当分母会出现单项占比 >100% 的怪象）
        share = f"{span.elapsed_ms / span_ms * 100:.1f}%" if span_ms else "-"
        print(
            pad_display(span.seq, 3, "right")
            + "  "
            + pad_display(span.node, 22)
            + pad_display(span.status, 10)
            + pad_display(format_ms(span.elapsed_ms), 10, "right")
            + "   "
            + pad_display(share, 7, "right")
        )
        if span.error:
            print(f"     └─ 报错：{span.error}")

    print(f"{'-' * 96}")
    print(
        f"合计 {len(spans)} 个节点 span = {format_ms(span_ms)}    "
        f"全链路墙钟 = {format_ms(header.total_ms)}"
    )
    print(
        "注：finalize 是记录者本身，不记自己的 span；召回与过滤是并行分支，"
        "因此 span 之和大于墙钟属正常。"
    )
    print()


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="链路追踪下钻")
    parser.add_argument("--last", type=int, default=5, help="列最近 N 条（默认 5）")
    parser.add_argument("--trace-id", type=str, default=None, help="查看指定 trace")
    parser.add_argument("--thread-id", type=str, default=None, help="查看指定会话最近一条")
    args = parser.parse_args()

    meta_mysql_client_manager.init()
    try:
        async with meta_mysql_client_manager.engine.connect() as conn:
            if args.trace_id:
                # 精确命中优先，其次按前缀匹配（列表只展示 8 位短 id，需支持直接下钻）
                rows = (
                    await conn.execute(
                        _SELECT_BY_TRACE_ID,
                        {
                            "trace_id": args.trace_id,
                            "trace_prefix": f"{args.trace_id}%",
                        },
                    )
                ).all()
            elif args.thread_id:
                rows = (
                    await conn.execute(_SELECT_BY_THREAD_ID, {"thread_id": args.thread_id})
                ).all()
            else:
                rows = (await conn.execute(_SELECT_RECENT, {"limit": args.last})).all()

            if not rows:
                print("没有查到追踪记录：确认 observability.enable=true，且已产生过问数。")
                return

            # 未指定单条时先给列表；指定了就直接下钻
            if not args.trace_id and not args.thread_id:
                _print_list(rows)
                return

            header = rows[0]
            spans = (
                await conn.execute(_SELECT_SPANS, {"trace_id": header.trace_id})
            ).all()
            _print_detail(header, spans)
    finally:
        await meta_mysql_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
