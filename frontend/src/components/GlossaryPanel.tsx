import { type FormEvent, useEffect, useState } from "react";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import {
  createGlossary,
  deleteGlossary,
  listGlossary,
  updateGlossary,
  type GlossaryTerm,
} from "../lib/agentApi";

type Props = {
  token: string;
  onClose: () => void;
};

type FormState = {
  id: string | null;
  term: string;
  definition: string;
  aliases: string;
  guidance: string;
  enabled: boolean;
};

const emptyForm: FormState = {
  id: null,
  term: "",
  definition: "",
  aliases: "",
  guidance: "",
  enabled: true,
};

export function GlossaryPanel({ token, onClose }: Props) {
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);

  async function reload() {
    setLoading(true);
    try {
      setTerms(await listGlossary(token));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  function startEdit(term: GlossaryTerm) {
    setForm({
      id: term.id,
      term: term.term,
      definition: term.definition,
      aliases: term.aliases.join("、"),
      guidance: term.guidance,
      enabled: term.enabled,
    });
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!form.term.trim() || !form.definition.trim()) return;
    setSaving(true);
    setError("");
    const payload = {
      term: form.term.trim(),
      definition: form.definition.trim(),
      aliases: form.aliases
        .split(/[、,，]/)
        .map((a) => a.trim())
        .filter(Boolean),
      guidance: form.guidance.trim(),
    };
    try {
      if (form.id) {
        await updateGlossary(token, form.id, { ...payload, enabled: form.enabled });
      } else {
        await createGlossary(token, payload);
      }
      setForm(emptyForm);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(term: GlossaryTerm) {
    if (!window.confirm(`确定删除术语「${term.term}」？`)) return;
    try {
      await deleteGlossary(token, term.id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/30 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-ink/15 bg-parchment">
        <div className="flex shrink-0 items-center justify-between border-b border-ink/10 px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">业务字典管理</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-ink/45 transition hover:text-ink"
            aria-label="关闭"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>

        {error ? (
          <div className="shrink-0 border-b border-ink/10 bg-[#f6e7e2] px-4 py-2 text-xs text-ink/80">
            {error}
          </div>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          {loading ? (
            <p className="text-xs text-ink/45">加载中…</p>
          ) : terms.length === 0 ? (
            <p className="text-xs text-ink/45">暂无术语，先新增一条。</p>
          ) : (
            <ul className="space-y-2">
              {terms.map((term) => (
                <li
                  key={term.id}
                  className="flex items-start justify-between gap-3 border border-ink/10 bg-white/50 px-3 py-2"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-ink">{term.term}</span>
                      {term.aliases.length > 0 ? (
                        <span className="truncate text-xs text-ink/45">
                          {term.aliases.join("、")}
                        </span>
                      ) : null}
                      <span
                        className={
                          term.enabled
                            ? "text-[10px] text-moss"
                            : "text-[10px] text-ink/40"
                        }
                      >
                        {term.enabled ? "启用" : "停用"}
                      </span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-xs text-ink/60">
                      {term.definition}
                    </p>
                    {term.guidance ? (
                      <p className="mt-0.5 line-clamp-2 text-xs text-ink/45">
                        口径：{term.guidance}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      type="button"
                      onClick={() => startEdit(term)}
                      className="text-ink/45 transition hover:text-ink"
                      aria-label="编辑"
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(term)}
                      className="text-ink/45 transition hover:text-[#b3452e]"
                      aria-label="删除"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <form
          onSubmit={handleSubmit}
          className="shrink-0 space-y-2 border-t border-ink/10 px-4 py-3"
        >
          <div className="flex items-center gap-2">
            <h3 className="text-xs font-semibold text-ink">
              {form.id ? "编辑术语" : "新增术语"}
            </h3>
            {form.id ? (
              <button
                type="button"
                onClick={() => setForm(emptyForm)}
                className="text-xs text-ink/45 hover:text-ink"
              >
                取消编辑
              </button>
            ) : null}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <input
              value={form.term}
              onChange={(e) => setForm({ ...form, term: e.target.value })}
              placeholder="术语（如 GMV）"
              className="border border-ink/15 bg-white/60 px-2 py-1.5 text-xs text-ink outline-none"
            />
            <input
              value={form.aliases}
              onChange={(e) => setForm({ ...form, aliases: e.target.value })}
              placeholder="别名，用、分隔"
              className="border border-ink/15 bg-white/60 px-2 py-1.5 text-xs text-ink outline-none"
            />
          </div>
          <input
            value={form.definition}
            onChange={(e) => setForm({ ...form, definition: e.target.value })}
            placeholder="定义（如 商品交易总额 = SUM(order_amount)）"
            className="w-full border border-ink/15 bg-white/60 px-2 py-1.5 text-xs text-ink outline-none"
          />
          <textarea
            value={form.guidance}
            onChange={(e) => setForm({ ...form, guidance: e.target.value })}
            placeholder="口径提示（如 跨品类求 GMV 默认合计，禁止 GROUP BY）"
            rows={2}
            className="w-full resize-none border border-ink/15 bg-white/60 px-2 py-1.5 text-xs text-ink outline-none"
          />
          <div className="flex items-center justify-between">
            <label className="inline-flex items-center gap-1.5 text-xs text-ink/60">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              启用
            </label>
            <button
              type="submit"
              disabled={saving || !form.term.trim() || !form.definition.trim()}
              className="inline-flex items-center gap-1 bg-ink px-3 py-1 text-xs font-medium text-parchment transition hover:bg-soot disabled:cursor-not-allowed disabled:opacity-40"
            >
              {form.id ? <Pencil className="h-3 w-3" aria-hidden="true" /> : <Plus className="h-3 w-3" aria-hidden="true" />}
              {saving ? "保存中" : form.id ? "保存修改" : "新增"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
