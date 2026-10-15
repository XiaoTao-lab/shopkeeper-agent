"""CSV from a previously authorized, masked and committed result snapshot only."""
import csv
import io
import re

from app.repositories.mysql.meta.history_repository import HistoryError

_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_INTEGER = re.compile(r"-?(?:0|[1-9][0-9]*)\Z")


def csv_cell(value, kind="string"):
    if value is None:
        return ""
    if kind in {"decimal", "integer"}:
        pattern = _NUMBER if kind == "decimal" else _INTEGER
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise HistoryError(409, "INVALID_RESULT_SNAPSHOT")
        return value
    text_value = str(value)
    if kind in {"string", "unknown"} and (text_value.lstrip().startswith(("=", "+", "-", "@")) or text_value.startswith(("\t", "\r", "\n"))):
        return "'" + text_value
    return text_value


def export_csv(run: dict) -> bytes:
    snapshot = run["terminal_event"]
    if run["status"] != "completed" or run["outcome"] != "result" or not snapshot or not snapshot.get("data"):
        raise HistoryError(409, "RESULT_NOT_EXPORTABLE")
    columns = snapshot.get("columns")
    if snapshot.get("schema_version") != 1 or not isinstance(columns, list):
        raise HistoryError(409, "RESULT_FORMAT_REQUIRES_MIGRATION")
    keys = [col["key"] for col in columns]
    if len(keys) != len(set(keys)):
        raise HistoryError(409, "INVALID_RESULT_SNAPSHOT")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([csv_cell(col["label"]) for col in columns])
    for row in snapshot["data"]:
        writer.writerow([csv_cell(row[key], col["type"]) for key, col in zip(keys, columns, strict=True)])
    return output.getvalue().encode("utf-8-sig")
