"""
指标口径查看脚本（离线）

直接读取 conf/meta_config.yaml 的 metrics 段，打印指标口径表与冻结后的口径文本，
用于快速核对"语义层"里每个指标的标准计算式是否符合预期。不依赖数据库与模型。

用法：
    # 打印指标口径总表
    uv run python -m app.scripts.show_metrics

    # 追加打印冻结后的口径约束文本（SQL 生成提示词里下发的原样）
    uv run python -m app.scripts.show_metrics --caliber
"""

import sys
from pathlib import Path

from omegaconf import OmegaConf

from app.agent.nodes.resolve_metric import compile_metric_caliber
from app.agent.state import MetricInfoState
from app.conf.meta_config import MetaConfig


def _print_table(metrics: list) -> None:
    """打印指标口径总表"""
    header = f"{'指标':<10}{'单位':<6}{'主表':<12}{'标准计算式':<40}别名"
    print(f"\n{'=' * 96}\n指标口径总表（语义层）\n{'=' * 96}")
    print(header)
    print("-" * 96)
    for m in metrics:
        alias = "、".join(m.alias or [])
        print(
            f"{m.name:<10}{m.unit or '-':<6}{m.base_table or '-':<12}"
            f"{m.formula or '(未标注口径)':<40}{alias}"
        )
    print(f"—— 共 {len(metrics)} 个指标")


def _to_state(metric) -> MetricInfoState:
    """把配置对象转成口径编译所需的状态结构"""
    return MetricInfoState(
        name=metric.name,
        description=metric.description,
        relevant_columns=list(metric.relevant_columns or []),
        alias=list(metric.alias or []),
        formula=metric.formula or "",
        base_table=metric.base_table or "",
        time_column=metric.time_column or "",
        default_filters=list(metric.default_filters or []),
        unit=metric.unit or "",
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    config_path = Path(__file__).parents[2] / "conf" / "meta_config.yaml"
    context = OmegaConf.load(config_path)
    meta_config: MetaConfig = OmegaConf.to_object(
        OmegaConf.merge(OmegaConf.structured(MetaConfig), context)
    )
    metrics = list(meta_config.metrics or [])

    _print_table(metrics)

    if "--caliber" in sys.argv[1:]:
        caliber_text = compile_metric_caliber([_to_state(m) for m in metrics])
        print(f"\n{'=' * 96}\n冻结后的口径约束文本（下发给 SQL 生成提示词）\n{'=' * 96}")
        print(caliber_text or "(无带口径的指标)")


if __name__ == "__main__":
    main()
