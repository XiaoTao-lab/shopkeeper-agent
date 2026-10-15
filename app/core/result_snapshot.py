"""Immutable, ordered and strictly JSON-serializable display snapshot."""
import json
import math
from datetime import date, datetime
from decimal import Decimal

MAX_ROWS = 1000
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_NUMERIC_CHARS = 128


def _type(values: list[object]) -> str:
    kinds = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            kinds.add("boolean")
        elif isinstance(value, int):
            kinds.add("integer")
        elif isinstance(value, Decimal):
            kinds.add("decimal")
        elif isinstance(value, float):
            kinds.add("number")
        elif isinstance(value, datetime):
            kinds.add("datetime")
        elif isinstance(value, date):
            kinds.add("date")
        elif isinstance(value, str):
            kinds.add("string")
        else:
            raise ValueError(f"不支持的结果值类型：{type(value).__name__}")
    if not kinds:
        return "unknown"
    if kinds <= {"integer", "decimal"}:
        return "decimal" if "decimal" in kinds else "integer"
    return next(iter(kinds)) if len(kinds) == 1 else "string"


def _encode(value: object, kind: str):
    if value is None:
        return None
    if kind == "string":
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        return str(value)
    if kind in {"decimal", "integer"}:
        encoded = format(value, "f") if kind == "decimal" else str(value)
        if len(encoded) > MAX_NUMERIC_CHARS or not Decimal(encoded).is_finite():
            raise ValueError("结果数值超出安全预算或非有限值")
        return encoded
    if kind == "number":
        if not math.isfinite(value):
            raise ValueError("结果含非有限浮点数")
        return value
    if kind in {"date", "datetime"}:
        return value.isoformat()
    if kind == "boolean":
        return value
    raise ValueError("未知类型的结果列包含非空数据")


def normalize_json(value):
    """Normalize metadata such as chart/explain, never silently stringify objects."""
    if isinstance(value, dict):
        return {str(k): normalize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json(v) for v in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("非有限 Decimal")
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("非有限浮点数")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"无法序列化展示信息：{type(value).__name__}")


def build_snapshot(columns: list[str], rows: list[dict], masked_columns: list[str], *, summary=None, chart=None, explain=None, permission=None, empty=False, message=None) -> dict:
    if len(set(columns)) != len(columns):
        raise ValueError("结果列名不唯一")
    visible = rows[:MAX_ROWS]
    truncated = len(rows) > len(visible)
    descriptors = []
    for key in columns:
        kind = "string" if key in masked_columns else _type([row.get(key) for row in visible])
        descriptors.append({"key": key, "label": key, "type": kind, "encoding": "string" if kind in {"integer", "decimal", "string", "date", "datetime"} else "json"})
    data = [{col["key"]: _encode(row.get(col["key"]), col["type"]) for col in descriptors} for row in visible]
    snapshot = {"type": "result", "schema_version": 1, "columns": descriptors, "data": data, "returned_row_count": len(data), "truncated": truncated}
    for key, value in {"summary": summary, "chart": chart, "explain": explain, "permission": permission, "message": message}.items():
        if value is not None:
            snapshot[key] = normalize_json(value)
    if empty:
        snapshot["empty"] = True
    if len(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_EVENT_BYTES:
        raise ValueError("结果快照超过 2 MiB，未保存不完整结果")
    return snapshot
