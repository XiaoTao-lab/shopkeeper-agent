"""
列级脱敏单元测试

覆盖 mask_rows 的核心行为：姓名/手机/邮箱三类打码、按列名自动选脱敏器、
命中列名回填、空配置/空数据放行、None 值跳过、非字典行原样保留。
"""

from app.core.mask import mask_rows


def test_name_masked() -> None:
    """姓名列按首字符保留、其余打码"""
    result = mask_rows([{"customer_name": "李伟"}], ["customer_name"])
    assert result.rows[0]["customer_name"] == "李*"
    assert result.masked_columns == ["customer_name"]


def test_single_char_name_kept() -> None:
    """单字姓名长度不足，原样保留"""
    result = mask_rows([{"customer_name": "王"}], ["customer_name"])
    assert result.rows[0]["customer_name"] == "王"


def test_phone_masked() -> None:
    """手机号保留前 3 后 4"""
    result = mask_rows([{"phone": "13800001234"}], ["phone"])
    assert result.rows[0]["phone"] == "138****1234"


def test_phone_column_by_keyword() -> None:
    """列名含 mobile 关键字也走手机号脱敏"""
    result = mask_rows([{"mobile_no": "13800001234"}], ["mobile_no"])
    assert result.rows[0]["mobile_no"] == "138****1234"


def test_email_masked() -> None:
    """邮箱保留首字符与域名"""
    result = mask_rows([{"email": "alice@example.com"}], ["email"])
    assert result.rows[0]["email"] == "a***@example.com"


def test_email_by_keyword() -> None:
    """列名含 mail 走邮箱脱敏"""
    result = mask_rows([{"mail_addr": "bob@x.com"}], ["mail_addr"])
    assert result.rows[0]["mail_addr"] == "b***@x.com"


def test_non_target_column_untouched() -> None:
    """未命中的列保持原值"""
    result = mask_rows([{"customer_name": "李伟", "GMV": 100}], ["customer_name"])
    assert result.rows[0]["GMV"] == 100
    assert result.masked_columns == ["customer_name"]


def test_empty_mask_columns_no_op() -> None:
    """无脱敏字段配置 → 原样返回"""
    rows = [{"customer_name": "李伟"}]
    result = mask_rows(rows, None)
    assert result.rows == rows
    assert result.masked_columns == []


def test_empty_mask_columns_list_no_op() -> None:
    result = mask_rows([{"customer_name": "李伟"}], [])
    assert result.rows == [{"customer_name": "李伟"}]


def test_none_value_skipped() -> None:
    """None 值不处理，也不计入命中列"""
    result = mask_rows([{"customer_name": None}], ["customer_name"])
    assert result.rows[0]["customer_name"] is None
    assert result.masked_columns == []


def test_multi_row_masked() -> None:
    """多行同名列表都脱敏，命中列去重"""
    result = mask_rows(
        [{"customer_name": "李伟"}, {"customer_name": "张敏"}], ["customer_name"]
    )
    assert result.rows[0]["customer_name"] == "李*"
    assert result.rows[1]["customer_name"] == "张*"
    assert result.masked_columns == ["customer_name"]


def test_substring_column_match() -> None:
    """结果列名与配置字段为包含关系也能命中（如 dim_customer.customer_name）"""
    result = mask_rows([{"dim_customer.customer_name": "李伟"}], ["customer_name"])
    assert result.rows[0]["dim_customer.customer_name"] == "李*"


def test_non_dict_row_kept() -> None:
    """非字典行原样保留，不报错"""
    result = mask_rows([("李伟", 100)], ["customer_name"])
    assert result.rows == [("李伟", 100)]


def test_non_list_input_returns_empty() -> None:
    """非列表输入（异常）→ 返回空列表，避免上游崩溃"""
    result = mask_rows(None, ["customer_name"])
    assert result.rows == []


def test_chinese_alias_matches_physical_name() -> None:
    """配置用物理列名 customer_name，结果列名是中文别名「客户名称」→ 应命中"""
    result = mask_rows([{"客户名称": "李伟"}], ["customer_name"])
    assert result.rows[0]["客户名称"] == "李*"
    assert result.masked_columns == ["客户名称"]


def test_region_name_not_masked_by_customer_rule() -> None:
    """region_name / 地区名称 不应被 customer_name 规则误伤"""
    result = mask_rows(
        [{"region_name": "华东", "地区名称": "华东", "客户名": "李伟"}],
        ["customer_name"],
    )
    assert result.rows[0]["region_name"] == "华东"
    assert result.rows[0]["地区名称"] == "华东"
    assert result.rows[0]["客户名"] == "李*"


def test_chinese_phone_alias() -> None:
    """中文别名「客户手机号」应走手机号脱敏"""
    result = mask_rows([{"客户手机号": "13800001234"}], ["phone"])
    assert result.rows[0]["客户手机号"] == "138****1234"
