"""
意图安全校验本地体验脚本

用于快速验证「用户问题 → 是否写意图」的判定结果。
默认只跑规则层（不联网）；加 --llm 时接入真实模型，验证隐含写意图的识别。

用法：
    # 只跑规则层（快速、离线）
    uv run python -m app.scripts.try_intent "把华东地区的GMV改为1"

    # 接入 LLM，验证规则未覆盖的隐含写意图
    uv run python -m app.scripts.try_intent --llm "帮我把滞销商品下架"

    # 不传问题则进入交互模式，回车逐条验证，输入 q 退出
    uv run python -m app.scripts.try_intent
"""

import asyncio
import sys

from app.core.intent_guard import check_intent


async def evaluate(query: str, use_llm: bool) -> None:
    """对单个问题执行意图判定并打印结果。

    Args:
        query: 用户问题
        use_llm: 是否接入真实 LLM 判别
    """
    llm = None
    if use_llm:
        # 延迟导入，避免离线模式下初始化模型连接
        from app.agent.llm import llm as real_llm

        llm = real_llm

    result = await check_intent(query, llm)
    print("-" * 64)
    print(f"输入问题 ：{query}")
    print(f"判定     ：[{'BLOCK' if result.is_write else 'PASS'}] "
          f"{'写操作意图，拦截' if result.is_write else '查询意图，放行'}")
    print(f"来源     ：{result.source}")
    print(f"原因     ：{result.reason}")


def main() -> None:
    """命令行入口：支持参数模式与交互模式。"""
    # Windows 控制台默认编码可能不是 utf-8，显式切换避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    args = sys.argv[1:]
    use_llm = False
    # 解析 --llm 开关，其余参数拼成用户问题
    if "--llm" in args:
        use_llm = True
        args = [a for a in args if a != "--llm"]

    if args:
        asyncio.run(evaluate(" ".join(args), use_llm))
        return

    mode = "规则 + LLM" if use_llm else "仅规则"
    print(f"意图安全校验体验（{mode}）—— 输入问题回车查看判定，输入 q 退出")
    while True:
        try:
            query = input("\n问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query:
            continue
        if query.lower() in {"q", "quit", "exit"}:
            break
        asyncio.run(evaluate(query, use_llm))


if __name__ == "__main__":
    main()
