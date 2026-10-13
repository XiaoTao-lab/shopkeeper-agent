"""
SQL 安全沙箱本地体验脚本

用于脱离数据库、LLM 和 Docker，直接输入 SQL 观察 guard_sql 的判定结果。
适合快速验证拦截规则是否符合预期。

用法：
    # 方式一：命令行直接传入
    uv run python -m app.scripts.try_guard "DELETE FROM fact_order"

    # 方式二：交互模式，回车逐条验证，输入 q 退出
    uv run python -m app.scripts.try_guard
"""

import sys

from app.core.sql_guard import guard_sql


def print_result(sql: str) -> None:
    """对单条 SQL 执行沙箱判定并打印结果。

    Args:
        sql: 待校验的 SQL 文本
    """
    result = guard_sql(sql)
    print("-" * 64)
    print(f"输入 SQL ：{sql}")
    if result.passed:
        tag = "通过（已加固）" if result.modified else "通过"
        print(f"判定     ：[PASS] {tag}")
        if result.modified:
            print(f"加固后   ：{result.sql}")
    else:
        print("判定     ：[BLOCK] 拦截")
        print(f"原因     ：{result.reason}")


def main() -> None:
    """命令行入口：支持参数模式与交互模式。"""
    # Windows 控制台默认编码可能不是 utf-8，显式切换避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8")

    # 参数模式：把命令行剩余参数拼成一条 SQL
    if len(sys.argv) > 1:
        print_result(" ".join(sys.argv[1:]))
        return

    # 交互模式：逐条输入、逐条查看判定
    print("SQL 安全沙箱体验 —— 输入 SQL 回车查看判定，输入 q 退出")
    while True:
        try:
            sql = input("\nSQL> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not sql:
            continue
        if sql.lower() in {"q", "quit", "exit"}:
            break
        print_result(sql)


if __name__ == "__main__":
    main()
