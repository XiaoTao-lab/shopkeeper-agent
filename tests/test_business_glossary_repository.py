from app.repositories.mysql.meta.business_glossary_repository import glossary_view


def test_glossary_view_decodes_aliases_and_preserves_enabled_state() -> None:
    item = glossary_view(
        {
            "id": "term-1",
            "term": "GMV",
            "definition": "成交总额",
            "aliases": '["交易额"]',
            "guidance": "SUM(order_amount)",
            "enabled": 1,
            "updated_by": "boss",
        }
    )

    assert item["aliases"] == ["交易额"]
    assert item["enabled"] is True
    assert item["updated_by"] == "boss"
