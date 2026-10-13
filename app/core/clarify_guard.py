"""
歧义澄清守卫（规则优先，离线可测）

问数场景里，用户常常只说一个"泛化度量词"（"金额""数量""数据"），却没有指明具体指标。
此时若交给 LLM 硬猜，它可能猜成 AOV、也可能猜成 GMV——用户拿到的数看着合理，
却未必是自己想要的那个，错得悄无声息。

本模块用一个纯规则做**高精度**歧义判定：只有当下面三个条件**同时成立**时才判定有歧义：

1. 问题里没有命中任何具体指标（指标名或别名）；
2. 召回候选里存在 ≥ 2 个不同的指标（才有"选哪个"的余地）；
3. 问题里出现了泛化度量词（说明用户"确实想算个数，只是没说清是哪个"）。

为什么要额外要求第 3 条：它把判定收紧成"想算个数但没说清"，避免对纯维度类问题
（如"列出所有地区"）过度追问——澄清太少会猜错，太多会烦人，这里优先保精度。
"""

from dataclasses import dataclass, field

# 泛化度量词：出现这些词、却未命中任何具体指标时，判定"想算个数但没说清指标"
_GENERIC_MEASURE_WORDS = [
    "金额",
    "总额",
    "收入",
    "营收",
    "数量",
    "人数",
    "指标",
    "数据",
    "情况",
    "表现",
    "业绩",
    "钱",
]

# 泛化词 → 类别线索：命中"钱类"词时，优先把金额类指标排在候选前面，
# 命中"数量类"词时优先排计数/件数类指标，让候选顺序更贴合用户语感。
_MONEY_WORDS = {"金额", "总额", "收入", "营收", "钱"}
_QUANTITY_WORDS = {"数量", "人数"}
_MONEY_HINTS = ("金额", "总额", "收入", "营收", "价", "元")
_QUANTITY_HINTS = ("数量", "件", "人数", "人")


@dataclass
class ClarifyOption:
    """一个候选指标（供用户点选）"""

    name: str  # 指标名，点击后作为用户回答
    description: str = ""  # 指标含义，作为选项说明展示


@dataclass
class Clarify:
    """一次澄清请求"""

    question: str  # 反问文案
    options: list[ClarifyOption] = field(default_factory=list)  # 候选指标
    reason: str = ""  # 判定理由（便于日志排查）


def _norm(text: str) -> str:
    """去空白 + 转小写，便于做大小写/空格无关的子串匹配"""
    return (text or "").strip().lower().replace(" ", "")


def _metric_terms(metric: dict) -> list[str]:
    """指标的全体称谓：指标名 + 全部别名"""
    return [metric.get("name") or "", *(metric.get("alias") or [])]


def mentioned_metric_names(query: str, metric_infos: list[dict]) -> list[str]:
    """返回问题里被**显式提到**的指标名（命中 name 或 alias），保持候选顺序并去重"""
    q = _norm(query)
    hits: list[str] = []
    for metric in metric_infos:
        name = (metric.get("name") or "").strip()
        if not name or name in hits:
            continue
        if any(_norm(term) and _norm(term) in q for term in _metric_terms(metric)):
            hits.append(name)
    return hits


def _dedup_candidates(metric_infos: list[dict]) -> list[dict]:
    """按指标名去重并保持召回顺序（同一指标可能被召回多次）"""
    seen: set[str] = set()
    candidates: list[dict] = []
    for metric in metric_infos:
        name = (metric.get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            candidates.append(metric)
    return candidates


def _relevance_hints(generic_hits: list[str]) -> tuple[str, ...]:
    """根据泛化词判断候选应按哪类线索优先排序"""
    if any(word in _MONEY_WORDS for word in generic_hits):
        return _MONEY_HINTS
    if any(word in _QUANTITY_WORDS for word in generic_hits):
        return _QUANTITY_HINTS
    return ()


def _rank_candidates(candidates: list[dict], hints: tuple[str, ...]) -> list[dict]:
    """按"与泛化词类别的相关度"稳定排序：相关度高的在前，同分保持召回顺序"""
    if not hints:
        return candidates

    def score(metric: dict) -> int:
        text = _norm(
            " ".join(
                [
                    metric.get("name") or "",
                    metric.get("description") or "",
                    metric.get("unit") or "",
                    *(metric.get("alias") or []),
                ]
            )
        )
        return 1 if any(_norm(hint) in text for hint in hints) else 0

    # sorted 是稳定排序，按 -score 排序即可让同分候选保持原召回顺序
    return sorted(candidates, key=lambda metric: -score(metric))


def detect_ambiguity(
    query: str, metric_infos: list[dict], max_options: int = 4
) -> Clarify | None:
    """判定问题是否存在"指标歧义"。

    Args:
        query: 用户本轮问题（多轮场景下为改写后的独立问题）
        metric_infos: 召回合并后的候选指标
        max_options: 反问里最多列出的候选指标个数

    Returns:
        存在歧义时返回 ``Clarify``；否则返回 None（放行给下游）
    """
    if not metric_infos:
        return None

    # 条件一：问题已明确说了某（几）个指标 → 没有歧义
    if mentioned_metric_names(query, metric_infos):
        return None

    candidates = _dedup_candidates(metric_infos)

    # 条件二：候选不足两个 → 没有"选哪个"的问题
    if len(candidates) < 2:
        return None

    # 条件三：问题里必须有泛化度量词，才认为"确实想算个数但没说清"
    generic_hits = [word for word in _GENERIC_MEASURE_WORDS if word in (query or "")]
    if not generic_hits:
        return None

    # 按泛化词类别做一次相关度排序，让候选更贴合用户语感（"金额"→金额类指标在前）
    ranked = _rank_candidates(candidates, _relevance_hints(generic_hits))

    options: list[ClarifyOption] = []
    for metric in ranked[:max_options]:
        options.append(
            ClarifyOption(
                name=metric["name"].strip(),
                description=(metric.get("description") or "").strip(),
            )
        )

    return Clarify(
        question=f"你提到的「{generic_hits[0]}」可能对应多个指标，请确认你想要的是哪一个？",
        options=options,
        reason=(
            "未命中具体指标；"
            f"泛化词={generic_hits}；"
            f"候选={[m['name'].strip() for m in ranked[:max_options]]}"
        ),
    )
