"""
列级脱敏

对不同角色可见的**列**做差异化处理：无权限的字段在结果返回给用户前打码。

和行级权限（改数据范围）互补：RLS 改的是"能看到哪些行"，本模块改的是"能看到哪些列的内容"。
按结果列名匹配需要脱敏的字段，并按字段语义（姓名 / 手机 / 邮箱）选择对应的打码方式。
由于 LLM 生成的 SQL 常把列写成中文别名（如 customer_name → 客户名称），
匹配采用「直接包含 + 语义归一」两级判定，确保配置物理列名时中文别名也能命中。

纯函数、无副作用，便于离线单测。
"""

from collections.abc import Iterable
from dataclasses import dataclass

# 全量打码时用到的占位符
_MASK_CHAR = "*"


def _mask_name(value: object) -> str:
    """姓名类脱敏：保留首字符（姓），其余打码。如 李伟 → 李*。"""
    text = str(value)
    if len(text) <= 1:
        return text
    return text[0] + _MASK_CHAR * (len(text) - 1)


def _mask_phone(value: object) -> str:
    """手机号类脱敏：保留前 3 后 4。如 13800001234 → 138****1234。"""
    text = str(value)
    if len(text) >= 7:
        return f"{text[:3]}{_MASK_CHAR * (len(text) - 7)}{text[-4:]}"
    return _MASK_CHAR * len(text)


def _mask_email(value: object) -> str:
    """邮箱类脱敏：保留首字符与域名。如 abc@x.com → a***@x.com。"""
    text = str(value)
    if "@" not in text:
        return _mask_name(text)
    local, _, domain = text.partition("@")
    if not local:
        return text
    return f"{local[0]}***@{domain}"


# 结果列名常被 LLM 改写成中文别名；建立「语义类 → 关键字」映射，
# 让物理列名（customer_name）与中文别名（客户名称）能归一到同一语义，
# 从而在「配置按物理列名、结果按中文别名」时仍能正确命中。
# 注意：关键字要精确到具体命名（如 customer_name / 客户名），
# 刻意不收录裸 "name"/"名称"，避免误伤 region_name / 地区名称。
_SEMANTIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("phone", ("phone", "mobile", "tel", "电话", "手机", "联系方式")),
    ("email", ("email", "mail", "邮箱", "邮件")),
    (
        "name",
        (
            "customer_name",
            "user_name",
            "member_name",
            "客户名称",
            "客户名",
            "客户姓名",
            "姓名",
            "用户名",
            "联系人",
            "收货人",
        ),
    ),
)


def _semantic_types(column: str) -> set[str]:
    """把列名归一到语义类集合（如 {"name"} / {"phone"}）。大小写不敏感。"""
    lowered = column.lower()
    types: set[str] = set()
    for key, keywords in _SEMANTIC_KEYWORDS:
        if any(keyword.lower() in lowered for keyword in keywords):
            types.add(key)
    return types


def _pick_masker(column: str):
    """根据列名的语义类挑选脱敏器，默认按姓名处理。"""
    types = _semantic_types(column)
    if "phone" in types:
        return _mask_phone
    if "email" in types:
        return _mask_email
    return _mask_name


def _column_matches(column: str, targets: Iterable[str]) -> bool:
    """结果列名是否命中待脱敏字段。

    两级判定：
    1. 直接包含匹配（忽略大小写）——配置字段与结果列名一致时命中；
    2. 语义匹配——两者归一到同一语义类时命中，兼容「配置 customer_name、
       结果列名 客户名称」这种物理名与别名不一致的情况。
    """
    lowered = column.lower()
    if any(target.lower() in lowered or lowered in target.lower() for target in targets):
        return True
    column_types = _semantic_types(column)
    if not column_types:
        return False
    return any(column_types & _semantic_types(target) for target in targets)


@dataclass
class MaskResult:
    """脱敏结果。

    Attributes:
        rows: 脱敏后的结果行
        masked_columns: 实际命中的结果列名（供前端提示与审计）
    """

    rows: list[dict]
    masked_columns: list[str]


def mask_rows(rows: object, mask_columns: list[str] | None) -> MaskResult:
    """对结果行里命中的列做脱敏。

    Args:
        rows: 结果集，形如 ``[{"customer_name": "李伟", "GMV": 100}]``
        mask_columns: 需要脱敏的字段名列表；为空表示该角色无需脱敏

    Returns:
        MaskResult: 脱敏后的行与实际命中的列名
    """
    if not mask_columns or not isinstance(rows, list):
        return MaskResult(rows=rows if isinstance(rows, list) else [], masked_columns=[])

    masked_columns: list[str] = []
    masked_rows: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            masked_rows.append(row)
            continue
        new_row = dict(row)
        for column, value in row.items():
            if value is None or not _column_matches(column, mask_columns):
                continue
            new_row[column] = _pick_masker(column)(value)
            if column not in masked_columns:
                masked_columns.append(column)
        masked_rows.append(new_row)

    return MaskResult(rows=masked_rows, masked_columns=masked_columns)
