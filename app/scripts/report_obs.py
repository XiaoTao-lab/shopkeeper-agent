"""
链路追踪聚合报表：回答"慢在哪 / 错在哪 / 贵在哪"

用法：
    # 近 7 天
    uv run python -m app.scripts.report_obs

    # 近 30 天
    uv run python -m app.scripts.report_obs --days 30

报表内容：
- 总体：问数次数、**业务成功率 / 拦截率 / 故障率**（三态分开，数字才不被安全带偏）、
  全链路 P50 / P95 / 最大耗时；
- 节点耗时排行：P95 排序，并给出占全链路耗时的比例（**瓶颈一眼可见**）；
- LLM 成本：调用次数、prompt / completion tokens、平均每次消耗；
- 失败归因：区分"节点真实报错"与"被安全拦截"，并列出真实故障的问数。

> 口径说明：**拦截是预期的安全行为**（越权请求被 1~2 秒挡下），不算业务失败；
> 只有节点真抛异常才是**故障**。因此业务成功率、耗时、token 成本**都只统计
> "进入链路"的问数**，被拦截的短请求单独用"拦截率"呈现，避免把性能与成本带偏。

与 show_trace.py 的分工：本脚本做**横向聚合**，show_trace 做**单次下钻**。
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta

from sqlalchemy import text

from app.clients.mysql_client_manager import meta_mysql_client_manager
from app.core.tracing import format_ms, pad_display, percentile

_SELECT_TRACES = text(
    """
    SELECT trace_id, status, total_ms, llm_calls, prompt_tokens, completion_tokens, `query`
    FROM `trace`
    WHERE created_at >= :since
    """
)

_SELECT_SPANS = text(
    """
    SELECT node, status, elapsed_ms
    FROM trace_span
    WHERE trace_id IN (
        SELECT trace_id FROM `trace`
        WHERE created_at >= :since AND status <> 'blocked'
    )
    """
)

# 耗时排行与失败归因都只展示前 N 名，避免刷屏
_TOP_N = 10


def _short_query(text_value: str | None, limit: int = 40) -> str:
    """把可能很长的查询压成一行短文本（失败归因列表用）"""
    if not text_value:
        return "-"
    flat = " ".join(str(text_value).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _print_overview(traces: list, days: int) -> None:
    """打印总体概况（分态：把安全拦截、真实故障从"失败"里拆出来）"""
    total = len(traces)
    ok = sum(1 for t in traces if t.status == "success")
    blocked = sum(1 for t in traces if t.status == "blocked")
    failed = sum(1 for t in traces if t.status == "error")
    clarified = sum(1 for t in traces if t.status == "clarify")

    # 业务成功率的分母排除"被拦截"：拦截是预期的安全行为，不是业务没做成。
    # 若不排除，安全防线拦得越尽责，这个数字反而越难看——口径就带偏了。
    business = total - blocked
    business_rate = ok / business * 100 if business else 0.0

    # 耗时同理只统计"真正进入链路"的问数：被拦截的请求 1~2 s 就结束了，
    # 混进来会把 P50 从几十秒拉到几秒，掩盖真实性能。
    durations = [t.total_ms or 0 for t in traces if t.status != "blocked"]

    print(f"{'=' * 78}")
    print(f"近 {days} 天  ·  共 {total} 次问数")
    print(f"{'=' * 78}")
    print(
        f"业务成功率 : {business_rate:.1f}%"
        f"   （成功 {ok} / 失败 {failed} / 澄清 {clarified}；分母已排除拦截）"
    )
    print(
        f"拦截率     : {blocked / total * 100:.1f}%"
        f"   （{blocked} 次越权请求被安全防线挡下，属预期行为）"
    )
    print(f"故障率     : {failed / total * 100:.1f}%   （{failed} 次节点真实报错）")
    if durations:
        print(
            f"全链路耗时 : P50 {format_ms(percentile(durations, 50))}   "
            f"P95 {format_ms(percentile(durations, 95))}   "
            f"最大 {format_ms(max(durations))}"
            f"   （仅含进入链路的 {len(durations)} 次）"
        )
    else:
        print("全链路耗时 : 窗口内没有进入链路的问数")
    print()


def _print_node_ranking(spans: list, total_ms_sum: int) -> None:
    """按 P95 打印节点耗时排行"""
    by_node: dict[str, list[int]] = {}
    for span in spans:
        by_node.setdefault(span.node, []).append(span.elapsed_ms or 0)

    ranking = sorted(
        ((node, values) for node, values in by_node.items()),
        key=lambda item: percentile(item[1], 95),
        reverse=True,
    )

    print("节点耗时排行（按 P95）")
    print(f"{'-' * 78}")
    print(
        pad_display("#", 3, "right")
        + "  "
        + pad_display("节点", 22)
        + pad_display("次数", 6, "right")
        + pad_display("P50", 10, "right")
        + pad_display("P95", 10, "right")
        + pad_display("耗时占比", 10, "right")
    )
    for index, (node, values) in enumerate(ranking[:_TOP_N], start=1):
        share = sum(values) / total_ms_sum * 100 if total_ms_sum else 0.0
        print(
            pad_display(index, 3, "right")
            + "  "
            + pad_display(node, 22)
            + pad_display(len(values), 6, "right")
            + pad_display(format_ms(percentile(values, 50)), 10, "right")
            + pad_display(format_ms(percentile(values, 95)), 10, "right")
            + pad_display(f"{share:.1f}%", 10, "right")
        )
    print("注：占比以「所有节点耗时之和」为分母。三路召回与两个过滤是并行分支，")
    print("    所以节点耗时之和会大于墙钟耗时，占比只用于横向排出瓶颈优先级。")
    print()


def _print_cost(traces: list) -> None:
    """打印 LLM 成本概况（只统计进入链路的问数）。

    被拦截的请求只花 1 次意图判别 LLM（约数百 token）就早停了，属安全开销；
    混进"平均每次问数消耗"会拉低真实业务成本，因此单独排除。
    """
    scoped = [t for t in traces if t.status != "blocked"]
    total = len(scoped)
    print("LLM 成本（仅含进入链路的问数）")
    print(f"{'-' * 78}")
    if not total:
        print("窗口内没有进入链路的问数。")
        print()
        return
    calls = sum(t.llm_calls or 0 for t in scoped)
    prompt = sum(t.prompt_tokens or 0 for t in scoped)
    completion = sum(t.completion_tokens or 0 for t in scoped)
    print(f"调用次数  : {calls:,}  （平均 {calls / total:.1f} 次/问数）")
    print(f"tokens    : in {prompt:,} / out {completion:,} / 合计 {prompt + completion:,}")
    print(f"平均消耗  : {(prompt + completion) / total:,.0f} tokens/问数")
    print()


def _print_failure(spans: list, traces: list) -> None:
    """打印失败归因：区分"节点真报错"与"被安全拦截" """

    # 真故障：状态为 error 的 trace（节点抛异常导致图中断）；列出来便于复现定位
    failed_traces = [t for t in traces if t.status == "error"]
    blocked = sum(1 for t in traces if t.status == "blocked")

    # 节点级报错：span.status == error。被拦截的节点是**正常 return**，span 记 success，
    # 所以这里统计到的全是真实异常，不会再和拦截混在一起。
    error_spans: dict[str, int] = {}
    for span in spans:
        if span.status == "error":
            error_spans[span.node] = error_spans.get(span.node, 0) + 1

    retried = sum(1 for span in spans if span.node == "correct_sql")

    print("失败归因")
    print(f"{'-' * 78}")
    if not failed_traces:
        print("真实故障 0 次：窗口内没有节点抛异常。")
    else:
        print(f"真实故障 {len(failed_traces)} 次（节点抛异常 / 图中断）：")
        for trace in failed_traces:
            print(f"  {trace.trace_id[:8]}  {_short_query(trace.query)}")
    for node, count in sorted(error_spans.items(), key=lambda i: i[1], reverse=True):
        print("  " + pad_display(node, 24) + f"报错 {count} 次")
    print(
        f"安全拦截 {blocked} 次（属预期行为，不计入故障）"
        f"—— 意图层 / SQL 沙箱挡下的越权请求"
    )
    print(
        f"correct_sql 触发 {retried} 次"
        f"（占问数 {retried / len(traces) * 100:.1f}%）—— SQL 首次校验未通过的次数"
    )
    print()


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="链路追踪聚合报表")
    parser.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")
    args = parser.parse_args()

    since = datetime.now() - timedelta(days=args.days)

    meta_mysql_client_manager.init()
    try:
        async with meta_mysql_client_manager.engine.connect() as conn:
            traces = (await conn.execute(_SELECT_TRACES, {"since": since})).all()
            if not traces:
                print(f"近 {args.days} 天没有追踪记录：确认 observability.enable=true 且已产生问数。")
                return

            spans = (await conn.execute(_SELECT_SPANS, {"since": since})).all()
            total_ms_sum = sum(span.elapsed_ms or 0 for span in spans)

            _print_overview(traces, args.days)
            _print_node_ranking(spans, total_ms_sum)
            _print_cost(traces)
            _print_failure(spans, traces)
    finally:
        await meta_mysql_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
