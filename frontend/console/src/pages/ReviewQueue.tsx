/**
 * The review queue: every NEEDS_INFO and AMBIGUOUS case, oldest first.
 *
 * One click to approve, or edit a field and approve. The reason a case is
 * here is the loudest line on the row -- a Cedar denial above everything --
 * so the operator never has to guess what the system was unsure about.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, feedback, isMock, queue } from "../api/client";
import { FIELD_ORDER, type QueueItem, type StructuredAddress } from "../api/types";
import { Empty, ErrorNote, EvidenceList, Icon, StatusPill, ageOf } from "../components/ui";

const LABEL: Record<string, string> = {
  building: "Building",
  street: "Street",
  sub_locality: "Sub-locality",
  locality: "Locality",
  city: "City",
  district: "District",
  state: "State",
  pincode: "Pincode",
};

export default function ReviewQueue() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

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
    setActing(item.order_id);
    setError(null);
    try {
      await feedback({ order_id: item.order_id, action: edits ? "edit" : action, actor: "console", edits: edits as never });
      setItems((prev) => prev.filter((q) => q.order_id !== item.order_id));
      setOpen(null);
      setDone(`${item.order_id} ${action === "reject" ? "rejected" : edits ? "corrected and approved" : "approved"}${action === "approve" ? " — the graph will learn from it" : ""}`);
      window.setTimeout(() => setDone(null), 4000);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setActing(null);
    }
  };

  const denied = items.filter((i) => i.review_reason).length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow">Needs a human</div>
          <h1 className="mt-1 text-xl font-extrabold tracking-tight">Review queue</h1>
          <p className="mt-1 max-w-[62ch] text-sm text-ink-2">Every case the system would not decide alone, oldest first, with the reason it is here. Approving one tells the landmark graph the delivery was right.</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="font-mono text-xl font-semibold tnum">{loading ? "—" : items.length}</div>
            <div className="text-2xs text-muted">
              open{denied ? ` · ${denied} by Cedar` : ""}
              {isMock ? " · mock" : ""}
            </div>
          </div>
          <button type="button" onClick={() => void load()} disabled={loading} className="btn-ghost" aria-label="Refresh the queue">
            <Icon name="refresh" className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </div>

      <div aria-live="polite" aria-atomic="true">
        {done && (
          <div className="flex items-center gap-2 rounded-md border border-leaf/40 bg-leaf/10 px-3 py-2 text-sm text-leaf">
            <Icon name="check" />
            {done}
          </div>
        )}
      </div>
      {error && <ErrorNote>{error}</ErrorNote>}

      {loading && (
        <div className="panel divide-y divide-hairline" aria-busy="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="grid grid-cols-[auto_1fr_auto] items-center gap-4 px-4 py-3">
              <div className="bone h-5 w-24 rounded-full" />
              <div className="space-y-2">
                <div className="bone h-3.5 w-1/2" />
                <div className="bone h-3 w-3/4" />
              </div>
              <div className="bone h-5 w-10" />
            </div>
          ))}
        </div>
      )}

      {!loading && !error && !items.length && (
        <div className="panel p-5">
          <Empty icon="check" title="Nothing needs a human right now" hint="Cases land here when confidence is below 0.80, two places fit equally, or Cedar denied a message. Try the Guardrails screen at 23:00 to see one arrive." />
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="panel divide-y divide-hairline" role="list">
          {items.map((item) => (
            <Row key={item.order_id} item={item} open={open === item.order_id} acting={acting === item.order_id} onToggle={() => setOpen(open === item.order_id ? null : item.order_id)} onAct={act} />
          ))}
        </div>
      )}
    </div>
  );
}

function Row({
  item,
  open,
  acting,
  onToggle,
  onAct,
}: {
  item: QueueItem;
  open: boolean;
  acting: boolean;
  onToggle: () => void;
  onAct: (item: QueueItem, action: "approve" | "reject", edits?: Partial<StructuredAddress>) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Partial<StructuredAddress>>({});
  const fromCedar = Boolean(item.review_reason);
  // A Cedar denial is the most recent and most actionable reason; it wins.
  const reason = item.review_reason ?? item.evidence.find((e) => /NEEDS_INFO|AMBIGUOUS|CONFLICT/.test(e)) ?? item.evidence.at(-1) ?? "";
  const dirty = Object.keys(draft).length > 0;
  const panelId = `case-${item.order_id}`;

  return (
    <div role="listitem" className={`transition-colors duration-fast ${open ? "bg-raised/60" : ""}`}>
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={panelId} className="grid w-full grid-cols-[1fr_auto] items-start gap-4 px-4 py-3 text-left">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill status={item.status} />
            <span className="font-mono text-xs text-ink-2">{item.order_id}</span>
            <span className="text-2xs text-muted">· {ageOf(item.created_at)}</span>
            {fromCedar && (
              <span className="pill border-deny/40 bg-deny/10 text-deny">
                <Icon name="shield" className="h-3 w-3" />
                Cedar DENY
              </span>
            )}
          </div>
          <div className="mt-1 truncate text-base font-medium">{item.summary || <span className="text-muted">no summary</span>}</div>
          <div className={`mt-0.5 truncate text-xs ${fromCedar ? "font-medium text-deny" : "text-muted"}`} title={reason}>
            {reason}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className="font-mono text-base font-semibold tnum">{item.confidence?.toFixed(2) ?? "—"}</div>
          {item.clarification && (
            <div className="mt-0.5 max-w-[16rem] truncate text-2xs text-saffron" title={item.clarification.question}>
              "{item.clarification.question}"
            </div>
          )}
        </div>
      </button>

      {open && (
        <div id={panelId} className="grid gap-5 border-t border-hairline px-4 py-4 md:grid-cols-2">
          <div>
            <div className="panel-title mb-2">Fields — edit if wrong</div>
            <div className="divide-y divide-hairline rounded-md border border-hairline bg-surface">
              {FIELD_ORDER.map((f) => (
                <div key={f} className="grid grid-cols-[92px_1fr] items-center gap-3 px-3 py-1.5">
                  <label htmlFor={`${panelId}-${f}`} className="text-xs text-muted">
                    {LABEL[f]}
                  </label>
                  <input
                    id={`${panelId}-${f}`}
                    defaultValue={(item.structured[f] as string | null) ?? ""}
                    onChange={(e) => setDraft((d) => ({ ...d, [f]: e.target.value || null }))}
                    className="w-full rounded-sm border border-transparent bg-transparent px-1.5 py-1 font-mono text-xs text-ink transition duration-fast focus:border-saffron focus:bg-surface focus:outline-none"
                    placeholder="—"
                  />
                </div>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => void onAct(item, "approve", dirty ? draft : undefined)} disabled={acting} className="btn !border-leaf !bg-leaf">
                <Icon name="check" />
                {acting ? "Saving…" : dirty ? "Save & approve" : "Approve"}
              </button>
              <button type="button" onClick={() => void onAct(item, "reject")} disabled={acting} className="btn-ghost">
                <Icon name="x" />
                Reject
              </button>
              <span className="ml-auto text-2xs text-muted">Approve emits DeliveryConfirmed → the learner</span>
            </div>
          </div>
          <div>
            <div className="panel-title mb-2">Why it is here</div>
            {fromCedar && (
              <div className="mb-3 rounded-md border border-deny/40 bg-deny/5 px-3 py-2 text-sm text-deny">
                <span className="font-semibold">Cedar denied outreach.</span> {item.review_reason}
              </div>
            )}
            <EvidenceList evidence={item.evidence} />
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-2xs text-muted">
              {item.digipin && <span>DIGIPIN {item.digipin}</span>}
              {item.geo && (
                <span>
                  {item.geo.lat.toFixed(5)}, {item.geo.lng.toFixed(5)} · {item.geo.source.replace("_", " ")}
                </span>
              )}
              <span>corr {item.correlation_id}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
