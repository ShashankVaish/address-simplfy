/**
 * The review queue: every NEEDS_INFO and AMBIGUOUS case, oldest first.
 *
 * One click to approve, or edit a field and approve. The reason confidence is
 * low is shown beside each case -- the operator should never have to guess
 * what the system was unsure about.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, feedback, queue } from "../api/client";
import { FIELD_ORDER, type QueueItem, type StructuredAddress } from "../api/types";
import { Card, EvidenceList, StatusPill } from "../components/ui";

export default function ReviewQueue() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await queue();
      setItems(r.items);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (item: QueueItem, action: "approve" | "reject", edits?: Partial<StructuredAddress>) => {
    try {
      await feedback({ order_id: item.order_id, action: edits ? "edit" : action, actor: "console", edits: edits as never });
      setItems((prev) => prev.filter((q) => q.order_id !== item.order_id));
      setOpen(null);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Review queue</h1>
        <div className="flex items-center gap-3 text-sm text-gray-500">
          <span>{items.length} open</span>
          <button onClick={() => void load()} className="rounded-lg bg-white px-3 py-1 ring-1 ring-gray-200 hover:bg-gray-50">Refresh</button>
        </div>
      </div>
      {error && <p className="rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
      {loading && <p className="text-sm text-gray-500">Loading…</p>}
      {!loading && !items.length && <Card><p className="text-sm text-gray-500">Nothing needs a human right now.</p></Card>}
      {items.map((item) => (
        <QueueRow key={item.order_id} item={item} open={open === item.order_id} onToggle={() => setOpen(open === item.order_id ? null : item.order_id)} onAct={act} />
      ))}
    </div>
  );
}

function QueueRow({
  item,
  open,
  onToggle,
  onAct,
}: {
  item: QueueItem;
  open: boolean;
  onToggle: () => void;
  onAct: (item: QueueItem, action: "approve" | "reject", edits?: Partial<StructuredAddress>) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Partial<StructuredAddress>>({});
  const age = ageOf(item.created_at);
  // A Cedar denial is the most recent and most actionable reason; it wins.
  const reason = item.review_reason ?? item.evidence.find((e) => /NEEDS_INFO|AMBIGUOUS|CONFLICT/.test(e)) ?? item.evidence.at(-1) ?? "";
  const fromCedar = Boolean(item.review_reason);
  const dirty = Object.keys(draft).length > 0;

  return (
    <Card>
      <button onClick={onToggle} className="flex w-full items-start justify-between gap-4 text-left">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StatusPill status={item.status} />
            <span className="font-mono text-xs text-gray-500">{item.order_id}</span>
            <span className="text-xs text-gray-400">· {age}</span>
          </div>
          <div className="mt-1 truncate font-medium">{item.summary || "—"}</div>
          <div className={`mt-0.5 truncate text-xs ${fromCedar ? "font-medium text-red-700" : "text-gray-500"}`}>{reason}</div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-sm">{item.confidence?.toFixed(2) ?? "—"}</div>
          {item.clarification && <div className="max-w-[16rem] truncate text-[11px] text-saffron" title={item.clarification.question}>“{item.clarification.question}”</div>}
        </div>
      </button>

      {open && (
        <div className="mt-4 grid gap-4 border-t border-gray-100 pt-4 md:grid-cols-2">
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Fields — edit if wrong</h3>
            <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-sm">
              {FIELD_ORDER.map((f) => (
                <div key={f} className="contents">
                  <label className="self-center text-gray-500">{f.replace("_", " ")}</label>
                  <input
                    defaultValue={(item.structured[f] as string | null) ?? ""}
                    onChange={(e) => setDraft((d) => ({ ...d, [f]: e.target.value || null }))}
                    className="rounded border border-gray-200 px-2 py-1 font-mono text-xs focus:border-saffron focus:outline-none"
                  />
                </div>
              ))}
            </div>
            <div className="mt-3 flex gap-2">
              <button onClick={() => void onAct(item, "approve", dirty ? draft : undefined)} className="rounded-lg bg-leaf px-3 py-1.5 text-sm font-semibold text-white hover:bg-green-800">
                {dirty ? "Save & approve" : "Approve"}
              </button>
              <button onClick={() => void onAct(item, "reject")} className="rounded-lg bg-white px-3 py-1.5 text-sm ring-1 ring-gray-300 hover:bg-gray-50">
                Reject
              </button>
            </div>
          </div>
          <div>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Why it is here</h3>
            <EvidenceList evidence={item.evidence} />
          </div>
        </div>
      )}
    </Card>
  );
}

function ageOf(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${m} min`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h` : `${Math.round(h / 24)} d`;
}
