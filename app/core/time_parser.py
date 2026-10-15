"""
时间维度解析（确定性）

把用户问题里的自然语言时间表达，解析成**半开区间** [start, end)：
包含起始日、不包含结束日。生成 SQL 时直接使用该区间，
避免把"上个月""最近7天"这类日期推算交给大模型而算错（跨年、闰年、含不含今天都是高发错点）。

策略与意图层、沙箱一致：**规则优先 + LLM 兜底**。
- 规则层覆盖高频表达，确定性、离线、零成本
- LLM 层只处理规则未命中的表达；兜底失败时返回 None（不加时间约束）——
  宁可少约束，也不下发一个错误的日期区间

规则层为纯函数，不依赖数据库与模型，便于离线单元测试。
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate

from app.core.log import logger
from app.prompt.prompt_loader import load_prompt

# 中文数字（时间表达里常见 一~十二）
_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


@dataclass
class TimeRange:
    """解析出的时间区间（半开区间 [start, end)）"""

    start: str  # 起始日（含），YYYY-MM-DD
    end: str  # 结束日（不含），YYYY-MM-DD
    granularity: str  # day/week/month/quarter/half_year/year
    expr: str  # 命中的原始表达
    source: str = "rule"  # rule / llm


def _num(token: str) -> int | None:
    """把阿拉伯数字或中文数字转成整数，无法识别时返回 None"""
    if token.isdigit():
        return int(token)
    return _CN_NUM.get(token)


def _full_year(token: str) -> int:
    """把年份 token 补全为四位年份，兼容"2025年"与"25年"两种写法

    两位数字按 Python ``strptime('%y')`` 的约定展开：00~68 → 20xx，69~99 → 19xx，
    因此 ``25`` → 2025。之所以要支持两位写法，是因为"25年"这类口语缩略很常见，
    交给 LLM 兜底既慢又不确定。
    """
    year = int(token)
    if len(token) == 4:
        return year
    return 2000 + year if year <= 68 else 1900 + year


def _month_start(d: date) -> date:
    """当月第一天"""
    return date(d.year, d.month, 1)


def _add_months(d: date, months: int) -> date:
    """在日期上加减月份，自动处理跨年与月末"""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _quarter_start(d: date) -> date:
    """当季第一天"""
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _r(start: date, end: date, granularity: str, expr: str) -> TimeRange:
    """构造半开区间结果"""
    return TimeRange(
        start=start.isoformat(),
        end=end.isoformat(),
        granularity=granularity,
        expr=expr,
    )


def parse_time_range_by_rule(query: str, today: date) -> TimeRange | None:
    """规则层：解析常见中文时间表达，命中返回 TimeRange，否则 None。

    Args:
        query: 用户原始问题
        today: 参考的"今天"，由调用方传入，便于测试与复现

    Returns:
        TimeRange 或 None
    """
    q = query or ""
    this_month = _month_start(today)
    this_quarter = _quarter_start(today)
    this_year = date(today.year, 1, 1)

    # 1) 最近 N 天 / 周 / 月 / 年（含当前周期）
    m = re.search(
        r"(?:最近|近|过去)\s*(\d{1,2}|[一二两三四五六七八九十]{1,2})\s*(天|日|周|个?月|年)", q
    )
    if m:
        n = _num(m.group(1))
        unit = m.group(2)
        if n and n > 0:
            expr = m.group(0)
            if unit in ("天", "日"):
                return _r(
                    today - timedelta(days=n - 1),
                    today + timedelta(days=1),
                    "day",
                    expr,
                )
            if unit == "周":
                return _r(
                    today - timedelta(days=7 * n - 1),
                    today + timedelta(days=1),
                    "week",
                    expr,
                )
            if unit in ("月", "个月"):
                return _r(
                    _add_months(this_month, -(n - 1)),
                    _add_months(this_month, 1),
                    "month",
                    expr,
                )
            return _r(
                date(today.year - n + 1, 1, 1),
                date(today.year + 1, 1, 1),
                "year",
                expr,
            )

    # 2) YYYY年 上半年 / 下半年（兼容两位年份，如"25年"）
    m = re.search(r"(\d{4}|\d{2})\s*年\s*(上半年|下半年)", q)
    if m:
        year = _full_year(m.group(1))
        if m.group(2) == "上半年":
            return _r(date(year, 1, 1), date(year, 7, 1), "half_year", m.group(0))
        return _r(date(year, 7, 1), date(year + 1, 1, 1), "half_year", m.group(0))

    # 3) 上半年 / 下半年（默认今年）
    m = re.search(r"(上半年|下半年)", q)
    if m:
        if m.group(1) == "上半年":
            return _r(this_year, date(today.year, 7, 1), "half_year", m.group(0))
        return _r(date(today.year, 7, 1), date(today.year + 1, 1, 1), "half_year", m.group(0))

    # 4) YYYY年 第X季度（兼容两位年份，如"25年第一季度"）
    m = re.search(r"(\d{4}|\d{2})\s*年\s*(?:第)?\s*([1-4一二三四])\s*季度", q)
    if m:
        year = _full_year(m.group(1))
        quarter = _num(m.group(2)) or 1
        start = date(year, 3 * (quarter - 1) + 1, 1)
        return _r(start, _add_months(start, 3), "quarter", m.group(0))

    # 5) YYYYQx / YYYY年Qx（兼容两位年份）
    m = re.search(r"(\d{4}|\d{2})\s*年?\s*[Qq]\s*([1-4])", q)
    if m:
        year = _full_year(m.group(1))
        start = date(year, 3 * (int(m.group(2)) - 1) + 1, 1)
        return _r(start, _add_months(start, 3), "quarter", m.group(0))

    # 6) X月到Y月（跨月区间，兼容"2025年2月到3月"与"2月到3月"）
    # 必须在"YYYY年M月 / M月"单月规则之前匹配，否则会被单月规则抢先命中，
    # 导致"2月到3月"只解析出"2月"、漏掉整个 3 月。
    m = re.search(
        r"(?:(\d{4}|\d{2})\s*年)?\s*(\d{1,2})\s*月\s*"
        r"(?:到|至|~|－|–|—|\-)\s*"
        r"(?:(\d{4}|\d{2})\s*年)?\s*(\d{1,2})\s*月",
        q,
    )
    if m:
        start_month = int(m.group(2))
        end_month = int(m.group(4))
        if 1 <= start_month <= 12 and 1 <= end_month <= 12:
            start_year = _full_year(m.group(1)) if m.group(1) else today.year
            if m.group(3):
                end_year = _full_year(m.group(3))
            else:
                end_year = start_year
                # 结束月早于起始月视为跨年：如"12月到1月" → 明年 1 月
                if end_month < start_month:
                    end_year += 1
            start = date(start_year, start_month, 1)
            # 右边界取"结束月的下个月初"，即包含整个结束月
            end = _add_months(date(end_year, end_month, 1), 1)
            return _r(start, end, "month", m.group(0))

    # 7) YYYY年M月（兼容两位年份）
    m = re.search(r"(\d{4}|\d{2})\s*年\s*(\d{1,2})\s*月", q)
    if m:
        start = date(_full_year(m.group(1)), int(m.group(2)), 1)
        return _r(start, _add_months(start, 1), "month", m.group(0))

    # 8) YYYY-MM / YYYY/MM
    m = re.search(r"(\d{4})\s*[-/]\s*(\d{1,2})(?!\d)", q)
    if m:
        start = date(int(m.group(1)), int(m.group(2)), 1)
        return _r(start, _add_months(start, 1), "month", m.group(0))

    # 9) YYYY年（兼容两位年份，如"25年"）
    m = re.search(r"(\d{4}|\d{2})\s*年", q)
    if m:
        year = _full_year(m.group(1))
        return _r(date(year, 1, 1), date(year + 1, 1, 1), "year", m.group(0))

    # 10) 本季度 / 上季度
    m = re.search(r"(本|这个|这|当)\s*季度", q)
    if m:
        return _r(this_quarter, _add_months(this_quarter, 3), "quarter", m.group(0))
    m = re.search(r"(上|前)\s*(?:一)?\s*个?\s*季度", q)
    if m:
        return _r(
            _add_months(this_quarter, -3), this_quarter, "quarter", m.group(0)
        )

    # 11) 本月 / 上月
    m = re.search(r"(本|这个|这|当)\s*个?\s*月(?:份)?", q)
    if m:
        return _r(this_month, _add_months(this_month, 1), "month", m.group(0))
    m = re.search(r"(上|前)\s*(?:一)?\s*个?\s*月(?:份)?", q)
    if m:
        return _r(_add_months(this_month, -1), this_month, "month", m.group(0))

    # 12) 本周 / 上周（周一为一周开始）
    monday = today - timedelta(days=today.weekday())
    m = re.search(r"(本|这个|这)\s*个?\s*(?:周|星期)", q)
    if m:
        return _r(monday, monday + timedelta(days=7), "week", m.group(0))
    m = re.search(r"(上|前)\s*(?:一)?\s*个?\s*(?:周|星期)", q)
    if m:
        return _r(monday - timedelta(days=7), monday, "week", m.group(0))

    # 13) 今年 / 去年
    if re.search(r"今年|本年|本年度", q):
        return _r(this_year, date(today.year + 1, 1, 1), "year", "今年")
    if re.search(r"去年|上年|上一年|前年", q):
        return _r(date(today.year - 1, 1, 1), this_year, "year", "去年")

    # 14) 今天 / 昨天 / 前天
    if re.search(r"今天|今日|本日", q):
        return _r(today, today + timedelta(days=1), "day", "今天")
    if re.search(r"昨天|昨日", q):
        return _r(today - timedelta(days=1), today, "day", "昨天")
    if re.search(r"前天", q):
        return _r(today - timedelta(days=2), today - timedelta(days=1), "day", "前天")

    # 15) M月（默认今年）
    m = re.search(r"(\d{1,2})\s*月", q)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            start = date(today.year, month, 1)
            return _r(start, _add_months(start, 1), "month", m.group(0))

    return None


async def _parse_by_llm(query: str, today: date, llm: BaseChatModel) -> TimeRange | None:
    """LLM 兜底层：规则未命中时，让模型解析时间表达。

    Args:
        query: 用户原始问题
        today: 参考的"今天"
        llm: 语言模型实例

    Returns:
        TimeRange 或 None
    """
    prompt = PromptTemplate(
        template=load_prompt("parse_time_range"),
        input_variables=["today", "query"],
    )
    chain = prompt | llm | JsonOutputParser()
    data: dict[str, Any] = await chain.ainvoke(
        {"today": today.isoformat(), "query": query}
    )

    if not data or not data.get("has_time"):
        return None

    start = str(data.get("start") or "").strip()
    end = str(data.get("end") or "").strip()
    # 严格校验日期格式，模型给不出合法区间时视为未命中
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
    except ValueError:
        logger.warning(f"时间兜底解析结果非法，忽略：start={start} end={end}")
        return None

    return TimeRange(
        start=start,
        end=end,
        granularity=str(data.get("granularity") or "range"),
        expr=str(data.get("expr") or query),
        source="llm",
    )


async def parse_time_range(
    query: str, today: date, llm: BaseChatModel | None = None
) -> TimeRange | None:
    """解析问题中的时间表达（规则优先，LLM 兜底）。

    Args:
        query: 用户原始问题
        today: 参考的"今天"
        llm: 兜底用的语言模型；为 None 时只走规则层

    Returns:
        TimeRange 或 None（无时间表达）
    """
    result = parse_time_range_by_rule(query, today)
    if result:
        return result

    if llm is None:
        return None

    try:
        return await _parse_by_llm(query, today, llm)
    except Exception as e:  # noqa: BLE001
        # 时间兜底失败不加约束，避免下发错误区间
        logger.warning(f"时间兜底解析失败，跳过时间约束：{e}")
        return None
