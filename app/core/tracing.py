"""
链路追踪核心（可观测）

一次问数 = 一条 **trace**，链路里每个业务节点 = 一条 **span**。
本模块只做三件事：**记账**（节点计时）、**累加**（token 用量）、**关联**（用 contextvar
把散落在各处的埋点串到同一次问数上）。

设计纪律（与 B2 审计一脉相承）：

- **零侵入**：节点骨架统一（都是 ``writer → try → 干活 → writer``），
  因此追踪在 ``graph.py`` 注册节点时统一包一层即可，**节点内部一行不改**；
- **旁路 fail-open**：追踪失败绝不影响问数（落库失败只在仓储层记日志）；
- **可关可采样**：``observability.enable`` 为假时 ``traced()`` 直接返回原函数，**零开销**；
  ``sample_rate`` 支持按比例抽样。

本模块的纯函数部分（状态归因 / token 提取 / 百分位 / 耗时格式化）不碰任何 IO，
便于离线单测，也可被 CLI 报表直接复用。
"""

import contextvars
import functools
import random
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from app.conf.app_config import app_config

# —— 一次问数的最终状态 ——
# success  正常出数
# blocked  被安全防线（意图层 / SQL 沙箱）挡下的越权请求，属**预期行为**
# error    节点真实抛异常（图崩了），属**故障**
# clarify  澄清反问，属**正常业务短路**
# blocked 与 error 必须分开：把"安全带拦下的"混进故障里，成功率会被安全带偏。
STATUS_SUCCESS = "success"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"
STATUS_CLARIFY = "clarify"

# span 上记录的异常信息长度上限，避免异常栈把一列撑爆
_MAX_ERROR_LEN = 500


@dataclass
class Span:
    """一个业务节点的执行记录"""

    node: str
    seq: int
    status: str
    elapsed_ms: int
    error: str | None = None


@dataclass
class TraceContext:
    """一次问数的追踪上下文（同一 asyncio 任务树内共享）"""

    trace_id: str
    thread_id: str | None = None
    request_id: str | None = None
    query: str | None = None
    role: str | None = None
    # 用 perf_counter 而非 wall clock：只关心间隔，不受系统时间调整影响
    started_at: float = field(default_factory=time.perf_counter)
    # 本次是否被采样（未采样则所有埋点直接空转）
    sampled: bool = True
    # 是否已落库：收尾节点 finalize 写成功、或异常兜底写成功后置真。
    # 服务层的异常兜底据此判断"是否还需要补写"，保证一条问数不会写两次。
    persisted: bool = False

    spans: list[Span] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def next_seq(self) -> int:
        """下一条 span 的序号（从 1 开始，按节点实际执行顺序递增）"""
        return len(self.spans) + 1

    def add_span(
        self, node: str, status: str, elapsed_ms: int, error: str | None = None
    ) -> Span:
        """追加一条节点执行记录"""
        span = Span(
            node=node,
            seq=self.next_seq(),
            status=status,
            elapsed_ms=elapsed_ms,
            error=error,
        )
        self.spans.append(span)
        return span

    def add_llm_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        """累加一次 LLM 调用的 token 用量"""
        self.llm_calls += 1
        # 负数没有意义（个别网关会返回 -1 表示未知），统一按 0 计
        self.prompt_tokens += max(prompt_tokens, 0)
        self.completion_tokens += max(completion_tokens, 0)

    @property
    def total_tokens(self) -> int:
        """本次问数消耗的总 token"""
        return self.prompt_tokens + self.completion_tokens

    @property
    def span_ms(self) -> int:
        """所有节点 span 耗时之和（不等于全链路耗时：分支未执行的节点不产生 span）"""
        return sum(span.elapsed_ms for span in self.spans)

    def elapsed_ms(self) -> int:
        """从开 trace 到现在的墙钟耗时"""
        return int((time.perf_counter() - self.started_at) * 1000)


# 用 ContextVar 而非全局变量：让并发请求各自的 trace 互不干扰。
# 节点在 asyncio 任务树内执行，会继承创建任务时的上下文，因此这里 set 一次即可全局可见。
_trace_ctx_var: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "trace_context", default=None
)


def should_sample(sample_rate: float, rng: random.Random | None = None) -> bool:
    """按采样比例决定本次是否采集（纯函数）。

    Args:
        sample_rate: 采样比例，>=1 恒采集，<=0 恒不采集
        rng: 可注入的随机源，便于测试时确定化

    Returns:
        本次是否采集
    """
    if sample_rate >= 1:
        return True
    if sample_rate <= 0:
        return False
    return (rng or random).random() < sample_rate


def new_trace(
    *,
    query: str | None = None,
    thread_id: str | None = None,
    request_id: str | None = None,
    role: str | None = None,
    sample_rate: float | None = None,
) -> TraceContext:
    """创建一次问数的追踪上下文（空参数时采样比例取配置值）"""
    rate = app_config.observability.sample_rate if sample_rate is None else sample_rate
    return TraceContext(
        trace_id=str(uuid.uuid4()),
        thread_id=thread_id,
        request_id=request_id,
        query=query,
        role=role,
        sampled=should_sample(rate),
    )


def set_trace(trace: TraceContext) -> contextvars.Token:
    """把 trace 放进当前上下文，返回用于还原的 token"""
    return _trace_ctx_var.set(trace)


def reset_trace(token: contextvars.Token) -> None:
    """还原上下文（请求结束时调用，避免跨请求串味）"""
    _trace_ctx_var.reset(token)


def get_trace() -> TraceContext | None:
    """取当前上下文的 trace；不在追踪范围内时返回 None（所有埋点据此空转）"""
    return _trace_ctx_var.get()


def resolve_run_status(state: dict) -> str:
    """按优先级判定一次问数的最终状态（纯函数，链路追踪与审计共用同一套归因）。

    优先级：**澄清反问 > 安全拦截 > 失败 > 成功**。

    - ``clarify``：澄清反问是**正常的业务短路**（用户需先选指标），不是失败；
    - ``blocked``：被意图层 / SQL 沙箱拦下的越权请求，是**预期的安全行为**，
      必须和"系统真出错"分开统计——否则安全防线拦得越尽责，成功率反而越低；
    - ``error``：节点真实抛异常（图崩了）；
    - ``success``：正常出数。

    Args:
        state: 链路最终状态（DataAgentState 的映射视图）

    Returns:
        ``clarify`` / ``blocked`` / ``error`` / ``success``
    """
    if state.get("clarify_question"):
        return STATUS_CLARIFY
    if state.get("blocked_by"):
        return STATUS_BLOCKED
    if state.get("error"):
        return STATUS_ERROR
    return STATUS_SUCCESS


def needs_fallback_persist(trace: TraceContext) -> bool:
    """判断这条 trace 是否需要由「异常兜底」路径补写（纯函数）。

    正常路径由收尾节点 finalize 落库并把 ``persisted`` 置真；一旦图执行中途抛异常，
    LangGraph 会中断、finalize **不会**执行，此时需要服务层兜底补写一条 error trace——
    否则真实故障会成为观测盲区：trace 里只剩下"被拦截"的记录，看不出系统崩过。

    Args:
        trace: 本次问数的追踪上下文

    Returns:
        需要补写返回 True；未采样或已落库返回 False
    """
    return trace.sampled and not trace.persisted


def extract_usage(response: Any) -> tuple[int, int]:
    """从 LangChain 的 ``LLMResult`` 里提取 ``(prompt_tokens, completion_tokens)``（纯函数）。

    两种来源，按可靠性排序：

    1. ``llm_output["token_usage"]``——OpenAI 兼容接口的标准返回；
    2. 消息上的 ``usage_metadata``——LangChain 标准化字段（新版模型普遍提供）。

    两处都取不到时返回 ``(0, 0)``：**宁可少记，也不能因缺字段影响问数**。
    """
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if prompt is not None or completion is not None:
        return int(prompt or 0), int(completion or 0)

    for generation_list in getattr(response, "generations", None) or []:
        for generation in generation_list:
            metadata = getattr(
                getattr(generation, "message", None), "usage_metadata", None
            )
            if metadata:
                return (
                    int(metadata.get("input_tokens") or 0),
                    int(metadata.get("output_tokens") or 0),
                )
    return 0, 0


def percentile(values: list[float], p: float) -> float:
    """线性插值百分位（纯函数，零依赖，CLI 报表与单测共用）。

    Args:
        values: 数值列表（内部排序，不改动入参）
        p: 百分位，取值 0~100

    Returns:
        对应的分位值；空列表返回 0.0
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * (p / 100)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return float(ordered[lower]) * (1 - weight) + float(ordered[upper]) * weight


def format_ms(ms: float | None) -> str:
    """把毫秒格式化成紧凑可读字符串（纯函数）：<1s 用 ms，>=1s 用 s"""
    if ms is None:
        return "-"
    value = float(ms)
    if value < 1000:
        return f"{int(value)} ms"
    return f"{value / 1000:.2f} s"


def display_width(text: object) -> int:
    """计算字符串的**显示宽度**（纯函数）。

    CLI 报表里中文按 1 个字符、2 个显示列宽计算，直接用 ``str.ljust`` 会导致
    含中文的列整体错位，因此这里按东亚宽字符（W/F）计 2 列。

    Args:
        text: 任意对象（内部转成字符串）

    Returns:
        显示宽度（列数）
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def pad_display(text: object, width: int, align: str = "left") -> str:
    """按显示宽度补空格对齐（纯函数，供 CLI 报表对齐中文列）。

    Args:
        text: 单元格内容
        width: 目标显示宽度
        align: ``left`` 左对齐（默认）或 ``right`` 右对齐

    Returns:
        补齐后的字符串；内容超宽时原样返回（不做截断）
    """
    value = str(text)
    padding = max(width - display_width(value), 0)
    if align == "right":
        return " " * padding + value
    return value + " " * padding


def traced(name: str, node: Callable) -> Callable:
    """把一个节点函数包成"带计时埋点"的版本。

    行为约定：

    - 成功与异常**都**记一条 span；异常记录后**继续抛出**，不改变既有控制流；
    - 当前没有 trace 上下文（离线直调 / 未开追踪）时**直接透传**，不引入任何额外开销；
    - ``observability.enable`` 为假时在注册阶段就返回原函数，连包装层都不存在。

    Args:
        name: 节点名，作为 span 名
        node: 原节点函数，签名 ``(state, runtime)``

    Returns:
        包装后的节点函数；关闭可观测时即原函数本身
    """
    if not app_config.observability.enable:
        return node

    # functools.wraps 会带上原函数的 __annotations__ 与 __wrapped__，
    # 这样 LangGraph 仍能按签名识别出 runtime 参数并正常注入 Runtime。
    @functools.wraps(node)
    async def wrapper(state, runtime):
        trace = get_trace()
        if trace is None or not trace.sampled:
            return await node(state, runtime)

        start = time.perf_counter()
        status = STATUS_SUCCESS
        error: str | None = None
        try:
            return await node(state, runtime)
        except Exception as e:
            status = STATUS_ERROR
            error = f"{type(e).__name__}: {e}"[:_MAX_ERROR_LEN]
            raise
        finally:
            # finally 里记账：无论成功、异常还是被取消，span 都不会漏
            elapsed = int((time.perf_counter() - start) * 1000)
            trace.add_span(name, status, elapsed, error)

    return wrapper


class TraceUsageCallback(BaseCallbackHandler):
    """LangChain 回调：每次 LLM 调用结束后，把 token 用量累加进当前 trace。

    为什么用回调而不是改节点：LLM 调用散落在 11 个节点里，逐个改侵入性太强；
    而回调只要**在模型上挂一次**（``app/agent/llm.py``），LangChain 就会把它
    下沉到每一次链式调用，真正做到零侵入。

    ``run_inline = True`` 是关键：默认 sync 回调在异步链路里会被丢到线程池执行，
    而 ``contextvars`` **不会**传播到线程池，会导致取不到 trace、token 永远为 0。
    改成内联执行后，回调与节点在同一上下文里，contextvar 才可见。
    """

    run_inline = True

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """LLM 调用结束：提取并累加 token 用量（任何异常都吞掉，观测不影响问数）"""
        try:
            trace = get_trace()
            if trace is None or not trace.sampled:
                return
            prompt_tokens, completion_tokens = extract_usage(response)
            trace.add_llm_usage(prompt_tokens, completion_tokens)
        except Exception:  # noqa: BLE001 - 观测是旁路能力，绝不允许影响主链路
            return
