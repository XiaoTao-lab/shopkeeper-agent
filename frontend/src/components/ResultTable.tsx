/**
 * 查询结果表格组件
 * 将后端返回的结构化数据归一化为可滚动表格，支持排序和分页
 */
import { ChevronDown, ChevronUp, Database, FileJson } from "lucide-react";
import { useMemo, useState } from "react";
import { makeComparator } from "../lib/decimalCompare";
import type { ResultColumn, ResultSnapshot } from "../types/agent";

type SortDirection = "asc" | "desc" | null;

type SortState = {
  columnKey: string | null;
  direction: SortDirection;
};

export function ResultTable({ data }: { data: ResultSnapshot | unknown }) {
  // 兼容旧格式：如果 data 不是结构化快照，归一化
  const snapshot = useMemo(() => {
    if (!data) return null;
    if (typeof data === "object" && "schema_version" in data && "columns" in data) {
      return data as ResultSnapshot;
    }
    // 旧格式：纯数组
    const rows = Array.isArray(data) ? data : [data];
    const columns: ResultColumn[] =
      rows.length > 0
        ? Object.keys(rows[0] as Record<string, unknown>).map((key) => ({
            key,
            label: key,
            type: "string" as const,
            encoding: "string" as const,
          }))
        : [];
    return {
      type: "result" as const,
      schema_version: 1 as const,
      columns,
      data: rows as Array<Record<string, unknown>>,
      returned_row_count: rows.length,
      truncated: false,
    };
  }, [data]);

  const [sort, setSort] = useState<SortState>({ columnKey: null, direction: null });
  const [pageSize, setPageSize] = useState(20);
  const [currentPage, setCurrentPage] = useState(1);

  if (!snapshot || snapshot.columns.length === 0) {
    return null;
  }

  const rows = snapshot.data;
  const columns = snapshot.columns;

  // 排序
  const sortedRows = useMemo(() => {
    if (!sort.columnKey || !sort.direction) return rows;
    const column = columns.find((c) => c.key === sort.columnKey);
    if (!column) return rows;
    const comparator = makeComparator(column.type, sort.direction);
    return [...rows].sort((a, b) => comparator(a[column.key], b[column.key]));
  }, [rows, columns, sort]);

  // 分页
  const totalPages = Math.max(1, Math.ceil(sortedRows.length / pageSize));
  const safePage = Math.min(currentPage, totalPages);
  const pageRows = sortedRows.slice((safePage - 1) * pageSize, safePage * pageSize);

  const handleSort = (columnKey: string) => {
    if (sort.columnKey === columnKey) {
      if (sort.direction === "asc") setSort({ columnKey, direction: "desc" });
      else if (sort.direction === "desc") setSort({ columnKey: null, direction: null });
      else setSort({ columnKey, direction: "asc" });
    } else {
      setSort({ columnKey, direction: "asc" });
    }
    setCurrentPage(1);
  };

  const handlePageSizeChange = (newSize: number) => {
    setPageSize(newSize);
    setCurrentPage(1);
  };

  return (
    <section className="mt-4 overflow-hidden border border-ink/10 bg-white/70 shadow-line">
      <div className="flex items-center justify-between border-b border-ink/10 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Database className="h-4 w-4 text-moss" aria-hidden="true" />
          查询结果
        </div>
        <div className="flex items-center gap-3 text-xs text-ink/55">
          <span>
            {snapshot.truncated ? `仅显示前 ${snapshot.returned_row_count} 行（已截断）` : `${snapshot.returned_row_count} 行`}
          </span>
          <select
            value={pageSize}
            onChange={(e) => handlePageSizeChange(Number(e.target.value))}
            className="border border-ink/10 bg-white px-2 py-1 text-xs outline-none"
          >
            <option value={20}>20 行/页</option>
            <option value={50}>50 行/页</option>
            <option value={100}>100 行/页</option>
          </select>
        </div>
      </div>

      <div className="max-h-[360px] overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-left text-sm">
          <thead className="sticky top-0 z-10 bg-[#efe6d8]">
            <tr>
              {columns.map((column) => (
                <th
                  key={column.key}
                  scope="col"
                  onClick={() => handleSort(column.key)}
                  className="cursor-pointer border-b border-ink/10 px-4 py-3 font-semibold text-ink/70 transition hover:bg-ink/5"
                >
                  <div className="flex items-center gap-1">
                    <span>{column.label}</span>
                    {sort.columnKey === column.key && (
                      sort.direction === "asc" ? (
                        <ChevronUp className="h-3 w-3" />
                      ) : sort.direction === "desc" ? (
                        <ChevronDown className="h-3 w-3" />
                      ) : null
                    )}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row, rowIndex) => (
              <tr key={rowIndex} className="odd:bg-white/45 even:bg-white/20">
                {columns.map((column) => (
                  <td key={column.key} className="border-b border-ink/5 px-4 py-3 text-ink/80">
                    {formatCell(row[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between border-t border-ink/10 px-4 py-2 text-xs text-ink/60">
          <span>
            第 {safePage} / {totalPages} 页
          </span>
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
              disabled={safePage === 1}
              className="rounded border border-ink/10 px-2 py-1 hover:bg-ink/5 disabled:opacity-40"
            >
              上一页
            </button>
            <button
              type="button"
              onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
              disabled={safePage === totalPages}
              className="rounded border border-ink/10 px-2 py-1 hover:bg-ink/5 disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
