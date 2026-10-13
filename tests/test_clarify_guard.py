"""
clarify_guard 歧义判定离线测试

覆盖两类：
1. 判定规则本身（命中/不命中的各种组合）；
2. **评测集回归护栏**——直接读取 conf 里的真实指标与 tests/eval 的 20 条评测问题，
   断言它们**一条都不会被误判为歧义**，从机制上保证 A3 不会拖累已有的 NL2SQL 准确率。
"""

from pathlib import Path

import yaml
from omegaconf import OmegaConf

from app.conf.meta_config import MetaConfig
from app.core.clarify_guard import detect_ambiguity, mentioned_metric_names

_ROOT = Path(__file__).resolve().parents[1]

# 精简单元测试用指标集
METRICS = [
    {
        "name": "GMV",
        "description": "成交金额总和",
        "alias": ["成交总额", "订单总额", "销售额", "营收", "交易总额"],
    },
    {
        "name": "AOV",
        "description": "每笔订单平均金额",
        "alias": ["客单价", "平均订单金额", "平均单价"],
    },
    {
        "name": "订单量",
        "description": "成交订单总数量",
        "alias": ["订单数", "订单总数"],
    },
]


def test_mentioned_by_name() -> None:
    """命中指标名"""
    assert mentioned_metric_names("统计华东地区的GMV", METRICS) == ["GMV"]


def test_mentioned_by_alias() -> None:
    """命中指标别名"""
    assert "GMV" in mentioned_metric_names("统计华东地区的销售额", METRICS)


def test_mentioned_multiple_in_order() -> None:
    """多指标按候选顺序返回且去重"""
    assert mentioned_metric_names("GMV和订单量分别是多少", METRICS) == ["GMV", "订单量"]


def test_explicit_metric_no_clarify() -> None:
    """问题已明确指标 → 不追问"""
    assert detect_ambiguity("统计华东地区的GMV", METRICS) is None


def test_generic_word_with_multi_candidates_clarifies() -> None:
    """泛化词 + 无命中 + 多候选 → 追问，且给出候选指标"""
    clarify = detect_ambiguity("统计华东地区的金额", METRICS)
    assert clarify is not None
    assert "金额" in clarify.question
    names = [option.name for option in clarify.options]
    assert "GMV" in names and "AOV" in names


def test_generic_word_single_candidate_no_clarify() -> None:
    """泛化词但只有单个候选 → 没有可选余地，不追问"""
    assert detect_ambiguity("统计华东地区的金额", [METRICS[0]]) is None


def test_no_generic_word_no_clarify() -> None:
    """没有泛化词（纯维度类问题）→ 不追问，避免过度打扰"""
    assert detect_ambiguity("华东地区的排名", METRICS) is None


def test_empty_candidates_no_clarify() -> None:
    """没有任何候选指标 → 不追问"""
    assert detect_ambiguity("统计华东地区的金额", []) is None


def test_case_and_space_insensitive() -> None:
    """大小写/空格不敏感：小写 gmv 也应命中"""
    assert detect_ambiguity("查一下 gmv", METRICS) is None


def test_options_are_deduped_and_capped() -> None:
    """候选去重且数量受 max_options 限制"""
    duplicated = [METRICS[0], METRICS[1], METRICS[0], METRICS[2]]
    clarify = detect_ambiguity("华东地区的金额", duplicated, max_options=2)
    assert clarify is not None
    assert len(clarify.options) == 2
    assert clarify.options[0].name == "GMV"
    assert clarify.options[1].name == "AOV"


def test_options_carry_description() -> None:
    """候选带出指标含义，便于前端展示"""
    clarify = detect_ambiguity("华东地区的金额", METRICS)
    assert clarify is not None
    assert clarify.options[0].description == "成交金额总和"


# 更贴近真实召回顺序的候选（金额类指标排在后面）
_REAL_ORDER_METRICS = [
    {
        "name": "下单客户数",
        "description": "产生订单的去重客户数量",
        "unit": "人",
        "alias": ["客户数"],
    },
    {
        "name": "订单量",
        "description": "成交订单的总数量",
        "unit": "笔",
        "alias": ["订单数"],
    },
    {
        "name": "销量",
        "description": "商品销售件数总和",
        "unit": "件",
        "alias": ["件数"],
    },
    {
        "name": "GMV",
        "description": "所有订单的成交金额总和",
        "unit": "元",
        "alias": ["销售额"],
    },
    {
        "name": "AOV",
        "description": "每笔订单的平均成交金额",
        "unit": "元",
        "alias": ["客单价"],
    },
]


def test_money_word_prioritizes_money_metrics() -> None:
    """泛化词是"金额"时，金额类指标应升到候选最前（即使召回顺序靠后）"""
    clarify = detect_ambiguity("统计华东地区的金额", _REAL_ORDER_METRICS, max_options=2)
    assert clarify is not None
    assert [option.name for option in clarify.options] == ["GMV", "AOV"]


def test_quantity_word_prioritizes_quantity_metrics() -> None:
    """泛化词是"数量"时，金额类指标应被排到候选之外"""
    clarify = detect_ambiguity("统计华东地区的数量", _REAL_ORDER_METRICS, max_options=2)
    assert clarify is not None
    names = [option.name for option in clarify.options]
    assert "GMV" not in names and "AOV" not in names


def test_eval_set_never_clarifies() -> None:
    """回归护栏：评测集 20 条问题全部不应被判定为歧义"""
    config = OmegaConf.load(_ROOT / "conf" / "meta_config.yaml")
    meta_config: MetaConfig = OmegaConf.to_object(
        OmegaConf.merge(OmegaConf.structured(MetaConfig), config)
    )
    metric_infos = [
        {
            "name": metric.name,
            "description": metric.description,
            "alias": list(metric.alias or []),
        }
        for metric in meta_config.metrics
    ]

    cases = yaml.safe_load(
        (_ROOT / "tests" / "eval" / "nl2sql_cases.yaml").read_text(encoding="utf-8")
    )["cases"]

    offenders = [
        case["id"]
        for case in cases
        if detect_ambiguity(case["question"], metric_infos) is not None
    ]
    assert offenders == [], f"评测集被误判为歧义：{offenders}"
