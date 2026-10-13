"""
链路追踪核心的单元测试（纯函数 + 埋点行为）

覆盖三类：

1. **纯函数**：状态归因、token 提取、百分位、耗时格式化、采样判定；
2. **记账行为**：span 序号递增、token 累加、异常也要记账；
3. **埋点纪律**：无 trace 上下文时零开销透传、采样未命中时不记、
   回调内异常不外泄、回调必须内联执行（否则 contextvar 不可见 → token 永远为 0）。
"""

import asyncio
import random
from types import SimpleNamespace

from app.core.tracing import (
    TraceContext,
    TraceUsageCallback,
    display_width,
    extract_usage,
    format_ms,
    get_trace,
    needs_fallback_persist,
    new_trace,
    pad_display,
    percentile,
    reset_trace,
    resolve_run_status,
    set_trace,
    should_sample,
    traced,
)

# —— 1. 纯函数 ——


def test_resolve_status_clarify_wins_over_error() -> None:
    """澄清反问优先于失败：澄清是正常业务短路，不该算成失败"""
    assert resolve_run_status({"clarify_question": "要看哪个指标？", "error": "x"}) == "clarify"


def test_resolve_status_error() -> None:
    """有 error 且无拦截标记 → 真故障"""
    assert resolve_run_status({"error": "guard blocked"}) == "error"


def test_resolve_status_blocked_from_intent() -> None:
    """被意图防线拦下：归为 blocked（预期的安全行为），而不是和真故障混为一谈"""
    assert (
        resolve_run_status({"error": "命中写意图", "blocked_by": "intent"}) == "blocked"
    )


def test_resolve_status_blocked_from_sql_guard() -> None:
    """被 SQL 沙箱拦下：同样归为 blocked"""
    assert (
        resolve_run_status({"error": "只读校验未通过", "blocked_by": "sql_guard"})
        == "blocked"
    )


def test_resolve_status_blocked_wins_over_error() -> None:
    """拦截标记优先于 error：拦截是预期的安全行为，不该被算成故障"""
    assert resolve_run_status({"error": "x", "blocked_by": "intent"}) == "blocked"


def test_resolve_status_clarify_wins_over_blocked() -> None:
    """澄清优先级最高（正常情况下二者不会同时出现，属防御性断言）"""
    assert (
        resolve_run_status({"clarify_question": "选哪个？", "blocked_by": "intent"})
        == "clarify"
    )


def test_resolve_status_error_requires_no_blocked_marker() -> None:
    """只有 error、拦截标记为空 → 真故障（清空标记后不残留上一轮的拦截态）"""
    assert (
        resolve_run_status({"error": "DB connection lost", "blocked_by": ""}) == "error"
    )


def test_resolve_status_success() -> None:
    """既无澄清也无 error → 成功"""
    assert resolve_run_status({}) == "success"


def test_needs_fallback_persist_when_sampled_and_unpersisted() -> None:
    """已采样且未落库 → 需要兜底补写（图执行中途崩溃时 finalize 不会执行）"""
    assert needs_fallback_persist(_make_trace()) is True


def test_needs_fallback_persist_false_after_persisted() -> None:
    """已落库（finalize 成功写过）→ 不再补写，保证一条问数只写一次"""
    trace = _make_trace()
    trace.persisted = True
    assert needs_fallback_persist(trace) is False


def test_needs_fallback_persist_false_when_unsampled() -> None:
    """未采样 → 不补写（与其它埋点一致：未采样全程空转）"""
    assert needs_fallback_persist(new_trace(sample_rate=0.0)) is False


def test_percentile_empty_returns_zero() -> None:
    """空列表返回 0.0，不抛异常"""
    assert percentile([], 95) == 0.0


def test_percentile_single_value() -> None:
    """只有一个值时分位数即该值"""
    assert percentile([42], 95) == 42.0


def test_percentile_median_by_interpolation() -> None:
    """偶数个值的 P50 走线性插值"""
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_percentile_p95_within_range() -> None:
    """P95 必须落在[min, max]内"""
    values = [float(i) for i in range(1, 101)]
    p95 = percentile(values, 95)
    assert 90 <= p95 <= 100


def test_format_ms_under_one_second() -> None:
    """小于 1 秒用 ms"""
    assert format_ms(820) == "820 ms"


def test_format_ms_over_one_second() -> None:
    """>=1 秒换算成秒并保留两位"""
    assert format_ms(6240) == "6.24 s"


def test_format_ms_none() -> None:
    """None 用 '-' 占位"""
    assert format_ms(None) == "-"


def test_should_sample_boundaries() -> None:
    """比例 >=1 恒采集，<=0 恒不采集"""
    assert should_sample(1.0) is True
    assert should_sample(1.5) is True
    assert should_sample(0.0) is False
    assert should_sample(-1) is False


def test_should_sample_uses_injected_rng() -> None:
    """注入固定随机源后结果可确定（0.5 比例下随机值 0.4 < 0.5 应采集）"""
    rng = random.Random()
    rng.random = lambda: 0.4  # type: ignore[method-assign]
    assert should_sample(0.5, rng) is True


def test_extract_usage_from_token_usage() -> None:
    """优先取 llm_output.token_usage（OpenAI 兼容接口的标准返回）"""
    response = SimpleNamespace(
        llm_output={"token_usage": {"prompt_tokens": 120, "completion_tokens": 30}}
    )
    assert extract_usage(response) == (120, 30)


def test_extract_usage_from_usage_key() -> None:
    """兼容部分网关用 usage 作为键名"""
    response = SimpleNamespace(
        llm_output={"usage": {"prompt_tokens": 7, "completion_tokens": 9}}
    )
    assert extract_usage(response) == (7, 9)


def test_extract_usage_from_usage_metadata() -> None:
    """llm_output 缺失时退回消息上的 usage_metadata"""
    message = SimpleNamespace(
        usage_metadata={"input_tokens": 55, "output_tokens": 22}
    )
    response = SimpleNamespace(llm_output=None, generations=[[SimpleNamespace(message=message)]])
    assert extract_usage(response) == (55, 22)


def test_extract_usage_returns_zero_when_missing() -> None:
    """两处都取不到时返回 (0, 0)：宁可少记，也不能因缺字段报错"""
    assert extract_usage(SimpleNamespace()) == (0, 0)
    assert extract_usage(None) == (0, 0)


# —— 2. 记账行为 ——


def _make_trace(**kwargs) -> TraceContext:
    """构造一个恒采样的 trace，避免测试受采样比例影响"""
    return new_trace(sample_rate=1.0, **kwargs)


def test_new_trace_sampled_by_rate() -> None:
    """sample_rate=1.0 必然采样；0.0 必然不采样"""
    assert _make_trace().sampled is True
    assert new_trace(sample_rate=0.0).sampled is False


def test_new_trace_ids_are_unique() -> None:
    """trace_id 不能重复（并发下要能区分不同问数）"""
    assert _make_trace().trace_id != _make_trace().trace_id


def test_add_span_increments_seq() -> None:
    """span 序号按执行顺序从 1 递增"""
    trace = _make_trace()
    trace.add_span("a", "success", 10)
    trace.add_span("b", "error", 20, "boom")
    assert [span.seq for span in trace.spans] == [1, 2]
    assert trace.spans[1].error == "boom"


def test_span_ms_sums_elapsed() -> None:
    """span_ms 是所有节点耗时之和"""
    trace = _make_trace()
    trace.add_span("a", "success", 10)
    trace.add_span("b", "success", 32)
    assert trace.span_ms == 42


def test_add_llm_usage_accumulates_and_clamps_negative() -> None:
    """token 累加；负数（部分网关用 -1 表示未知）按 0 计"""
    trace = _make_trace()
    trace.add_llm_usage(100, 20)
    trace.add_llm_usage(-1, 5)
    assert trace.llm_calls == 2
    assert trace.prompt_tokens == 100
    assert trace.completion_tokens == 25
    assert trace.total_tokens == 125


def test_elapsed_ms_non_negative() -> None:
    """刚创建的 trace 耗时 >= 0"""
    assert _make_trace().elapsed_ms() >= 0


# —— 3. 埋点纪律 ——


def test_traced_records_success_span() -> None:
    """节点执行成功：记一条 success 的 span"""

    async def node(state, runtime):
        return {"ok": True}

    trace = _make_trace()
    token = set_trace(trace)
    try:
        result = asyncio.run(traced("demo", node)({}, None))
    finally:
        reset_trace(token)

    assert result == {"ok": True}
    assert [(s.node, s.status) for s in trace.spans] == [("demo", "success")]


def test_traced_records_error_span_and_reraises() -> None:
    """节点抛异常：记一条 error span，且异常必须继续抛出（不改变控制流）"""

    async def node(state, runtime):
        raise ValueError("校验失败")

    trace = _make_trace()
    token = set_trace(trace)
    try:
        try:
            asyncio.run(traced("demo", node)({}, None))
            raise AssertionError("异常必须继续抛出")
        except ValueError:
            pass
    finally:
        reset_trace(token)

    assert len(trace.spans) == 1
    assert trace.spans[0].status == "error"
    assert "ValueError" in (trace.spans[0].error or "")


def test_traced_passthrough_without_trace_context() -> None:
    """没有 trace 上下文（离线直调 / 未开追踪）：透传结果且不产生任何记账"""
    calls = []

    async def node(state, runtime):
        calls.append(1)
        return {"ok": True}

    assert asyncio.run(traced("demo", node)({}, None)) == {"ok": True}
    assert calls == [1]


def test_traced_skips_span_when_not_sampled() -> None:
    """未采样：节点照常执行，但不记 span（省开销）"""

    async def node(state, runtime):
        return "done"

    trace = new_trace(sample_rate=0.0)
    token = set_trace(trace)
    try:
        assert asyncio.run(traced("demo", node)({}, None)) == "done"
    finally:
        reset_trace(token)

    assert trace.spans == []


def test_traced_returns_original_when_disabled() -> None:
    """关闭可观测时返回原函数本身（连包装层都不存在）"""

    async def node(state, runtime):
        return None

    from app.conf.app_config import app_config
    from app.core import tracing as tracing_module

    original_enable = app_config.observability.enable
    try:
        # 直接改配置对象模拟"关闭"
        app_config.observability.enable = False
        assert tracing_module.traced("demo", node) is node
    finally:
        app_config.observability.enable = original_enable


def test_usage_callback_accumulates_when_trace_present() -> None:
    """回调在追踪上下文内：把 token 累加进当前 trace"""
    trace = _make_trace()
    token = set_trace(trace)
    try:
        TraceUsageCallback().on_llm_end(
            SimpleNamespace(
                llm_output={"token_usage": {"prompt_tokens": 10, "completion_tokens": 2}}
            )
        )
    finally:
        reset_trace(token)

    assert (trace.llm_calls, trace.total_tokens) == (1, 12)


def test_usage_callback_is_silent_without_trace() -> None:
    """没有 trace 上下文（如离线脚本）时回调静默返回，不抛异常"""
    TraceUsageCallback().on_llm_end(SimpleNamespace())


def test_usage_callback_swallows_internal_error() -> None:
    """回调内部异常必须吞掉：观测是旁路，绝不能影响问数"""

    class BoomResponse:
        """访问 llm_output 即抛错，模拟网关返回了意料之外的结构"""

        @property
        def llm_output(self):
            raise RuntimeError("unexpected response shape")

    trace = _make_trace()
    token = set_trace(trace)
    try:
        # 不抛异常即为通过；同时确认没有把脏数据记进 trace
        TraceUsageCallback().on_llm_end(BoomResponse())
    finally:
        reset_trace(token)

    assert trace.llm_calls == 0


def test_usage_callback_runs_inline() -> None:
    """回调必须内联执行。

    默认 sync 回调在异步链路里会被丢进线程池，而 contextvars 不会传播到线程池，
    会导致取不到 trace、token 永远记不上。因此这里锁死 run_inline=True。
    """
    assert TraceUsageCallback().run_inline is True


def test_get_trace_after_reset_is_none() -> None:
    """reset 之后上下文里没有 trace（避免并发请求之间串味）"""
    token = set_trace(_make_trace())
    assert get_trace() is not None
    reset_trace(token)
    assert get_trace() is None


# —— 4. 报表格式化（中文列宽） ——


def test_display_width_counts_cjk_as_two() -> None:
    """中文按 2 个显示列计算（否则 CLI 报表会整列错位）"""
    assert display_width("华东") == 4
    assert display_width("ab") == 2
    assert display_width("华东ab") == 6
    assert display_width(123) == 3


def test_pad_display_left_and_right_align() -> None:
    """按显示宽度补空格：左对齐补尾、右对齐补头"""
    assert pad_display("华东", 6) == "华东  "
    assert pad_display("7", 4, "right") == "   7"


def test_pad_display_never_truncates_overwide_text() -> None:
    """内容超宽时原样返回，不做截断（宁可错位也不丢信息）"""
    assert pad_display("华东华南", 4) == "华东华南"
