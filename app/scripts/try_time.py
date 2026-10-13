"""
时间解析本地体验脚本

默认只跑规则层（离线、确定性）；加 --llm 时对规则未命中的表达走模型兜底。
用 --today 指定参考日期，方便验证跨年、季度边界等场景。

用法：
    # 规则层（默认今天为系统日期）
    uv run python -m app.scripts.try_time "上个月"

    # 指定参考日期，验证跨年边界
    uv run python -m app.scripts.try_time --today 2026-01-15 "上季度"

    # 规则未命中时走 LLM 兜底
    uv run python -m app.scripts.try_time --llm "去年双十一那一周"

    # 不传问题则进入交互模式，输入 q 退出
    uv run python -m app.scripts.try_time
"""

import argparse
import asyncio
import sys
from datetime import date

from app.core.time_parser import parse_time_range


async def evaluate(query: str, today: date, use_llm: bool) -> None:
    """解析单个问题的时间表达并打印结果"""
    llm = None
    if use_llm:
        from app.agent.llm import llm as real_llm

        llm = real_llm

    result = await parse_time_range(query, today, llm)
    print("-" * 72)
    print(f"输入问题 ：{query}")
    print(f"参考日期 ：{today.isoformat()}")
    if result is None:
        print("判定     ：未识别到时间表达（不加时间约束）")
        return
    print(f"时间区间 ：[{result.start}, {result.end})   （含起始、不含结束）")
    print(f"粒度     ：{result.granularity}")
    print(f"原始表达 ：{result.expr}")
    print(f"来源     ：{result.source}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="时间表达解析体验")
    parser.add_argument("query", nargs="*", help="要解析的问题")
    parser.add_argument("--today", help="参考日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--llm", action="store_true", help="规则未命中时走 LLM 兜底")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()

    if args.query:
        asyncio.run(evaluate(" ".join(args.query), today, args.llm))
        return

    mode = "规则 + LLM" if args.llm else "仅规则"
    print(f"时间解析体验（{mode}，参考日期 {today.isoformat()}）—— 输入问题回车查看，q 退出")
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
        asyncio.run(evaluate(query, today, args.llm))


if __name__ == "__main__":
    main()
