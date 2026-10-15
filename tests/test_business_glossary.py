import pytest

from app.services.business_glossary_service import normalize_aliases, render_glossary


def test_render_glossary_keeps_only_enabled_terms_and_bounds_its_size() -> None:
    rendered = render_glossary(
        [
            {
                "term": "GMV",
                "definition": "成交总额",
                "aliases": ["交易额"],
                "guidance": "标准口径为 SUM(order_amount)",
                "enabled": True,
            },
            {
                "term": "停用术语",
                "definition": "不应进入模型上下文",
                "aliases": [],
                "guidance": "",
                "enabled": False,
            },
        ]
    )

    assert "GMV" in rendered
    assert "交易额" in rendered
    assert "停用术语" not in rendered
    assert "仅用于解释业务术语" in rendered


def test_normalize_aliases_deduplicates_and_rejects_the_term_itself() -> None:
    assert normalize_aliases("GMV", ["交易额", " 交易额 ", "成交总额"]) == ["交易额", "成交总额"]

    with pytest.raises(ValueError, match="不能包含术语本身"):
        normalize_aliases("GMV", ["GMV"])
