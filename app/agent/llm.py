"""
电商问数 Agent 使用的大模型实例

集中初始化一个 OpenAI 兼容的 Chat Model，供节点或本地测试直接复用。

性能背景：当前模型（qwen3.8-flash）默认开启思维链（thinking），实测每次调用约
97% 的输出 token 都花在 reasoning 上——一次关键词扩展要“想” 480 token、耗时 8.32s，
而真正答案只有 8 个 token。同一个 prompt 关掉 thinking 后 0.73s 返回、答案不变，快 11 倍。

因此这里初始化两个实例，并按节点分流：
- llm       ：保留 thinking，给“需要多想一步”的节点（如 SQL 生成）。
- llm_fast  ：关闭 thinking，给关键词扩展、表/指标过滤等“轻任务”节点。

哪些节点走 llm_fast 由配置 llm.no_think_nodes 决定：改配置即可开关，无需改代码。
"""

from langchain.chat_models import init_chat_model

from app.conf.app_config import app_config
from app.core.tracing import TraceUsageCallback

# 关闭思维链的请求参数（阿里云 MaaS / 通义千问系列兼容 OpenAI 协议的写法）
THINKING_OFF_BODY = {"enable_thinking": False}


def _build_llm(**overrides):
    """按统一配置构造一个 Chat Model，并把追踪回调挂一次。

    追踪回调只挂在这里：LangChain 会把它下沉到每一次链式调用（prompt | llm | parser），
    因此用到大模型的节点无需任何改动。观测未开启或不在追踪范围内时，回调内部直接空转。

    Args:
        **overrides: 透传给模型构造器的额外参数，例如 extra_body。

    Returns:
        已挂载 TraceUsageCallback 的 Chat Model 实例。
    """
    return init_chat_model(
        model=app_config.llm.model_name,
        # 硅基流动等服务兼容 OpenAI 协议时，可以使用 openai provider 接入
        model_provider="openai",
        base_url=app_config.llm.base_url,
        api_key=app_config.llm.api_key,
        # 字段扩展、SQL 生成更看重稳定性，所以这里关闭随机发散
        temperature=0,
        **overrides,
    ).with_config(callbacks=[TraceUsageCallback()])


# 默认模型：保留思维链，供 SQL 生成等需要推理的节点使用
llm = _build_llm()

# 轻量模型：关闭思维链，供关键词扩展、表/指标过滤等“轻任务”节点使用
llm_fast = _build_llm(extra_body=THINKING_OFF_BODY)


def get_llm(node: str):
    """按节点名返回应使用的模型实例。

    把“哪个节点关 thinking”这件事收敛到配置里，节点侧只声明自己的名字，
    这样回滚单个节点只需从配置删掉名字，不用改节点代码。

    Args:
        node: 节点名（与配置 llm.no_think_nodes 中的取值一致）。

    Returns:
        被配置列为“轻任务”的节点返回关闭思维链的 llm_fast，其余返回 llm。
    """
    return llm_fast if node in app_config.llm.no_think_nodes else llm


if __name__ == "__main__":
    # 本地快速验证 LLM 配置是否能正常调用
    print(llm.invoke("你好").content)
