/**
 * Guardrails: ask Cedar whether we may contact a customer, right now.
 *
 * The controls are exactly the policy's inputs, so what the operator sees is
 * what the engine saw. The decision names the policy that decided it and the
 * viewer lights up that clause: verdict and proof on one screen.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, authorize, isMock } from "../api/client";
import type { AuthorizeRequest, AuthzDecision } from "../api/types";
import { CedarViewer, clauseFor } from "../components/CedarViewer";
import { Empty, ErrorNote, Icon, Panel } from "../components/ui";

const DEFAULT: AuthorizeRequest = {
  order_id: "DEMO-23",
  action: "contactCustomer",
  confidence: 0.55,
  local_hour: 23,
  channel: "sms",
  opted_out: false,
  messages_sent_for_order: 0,
  principal: "clarifier",
  summary: "behind Shiv Mandir, Ramesh Nagar, 110015",
};

const WINDOW: [number, number] = [9, 20];
const THRESHOLD = 0.8;
const pad = (n: number) => `${n < 10 ? "0" : ""}${n}:00`;

export default function Guardrails() {
  const [req, setReq] = useState<AuthorizeRequest>(DEFAULT);
  const [decision, setDecision] = useState<AuthzDecision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);

  const set = <K extends keyof AuthorizeRequest>(k: K, v: AuthorizeRequest[K]) => {
    setReq((r) => ({ ...r, [k]: v }));
    // A changed input makes the last answer stale; keep it visible but say so.
    if (decision) setStale(true);
  };

  const ask = useCallback(async () => {
    if (busy || !req.order_id.trim()) return;
    setBusy(true);
    setError(null);
    try {
      setDecision(await authorize(req));
      setStale(false);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }, [req, busy]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void ask();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ask]);

  // What the inputs already say, before the engine is asked.
  const outsideHours = req.local_hour < WINDOW[0] || req.local_hour > WINDOW[1];
  const aboveThreshold = req.confidence >= THRESHOLD;
  const clause = useMemo(() => clauseFor(decision), [decision]);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow">Cedar authorisation</div>
          <h1 className="mt-1 text-xl font-extrabold tracking-tight">Guardrails</h1>
          <p className="mt-1 max-w-[62ch] text-sm text-ink-2">Every outbound message is authorised first. Policy is a text file; a denial names the policy and the clause.</p>
        </div>
        {isMock && <span className="text-xs text-muted">mock — same rules, evaluated locally</span>}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        {/* ------------------------------------------------ the situation */}
        <Panel title="The situation" aside="the policy's inputs, nothing more">
          <form
            className="space-y-5"
            onSubmit={(e) => {
              e.preventDefault();
              void ask();
            }}
          >
            <div>
              <label htmlFor="order" className="text-xs font-medium text-ink-2">
                Order
              </label>
              <input id="order" value={req.order_id} onChange={(e) => set("order_id", e.target.value)} className="field mt-1 font-mono" autoComplete="off" spellCheck={false} />
            </div>

            {/* time slider with the permitted window drawn on the track */}
            <div>
              <div className="flex items-baseline justify-between">
                <label htmlFor="hour" className="text-xs font-medium text-ink-2">
                  Customer's local time
                </label>
                <span className={`font-mono text-sm font-semibold tnum ${outsideHours ? "text-deny" : "text-leaf"}`}>
                  {pad(req.local_hour)} · {outsideHours ? "outside" : "inside"} contact hours
                </span>
              </div>
              <div className="relative mt-1">
                <div className="pointer-events-none absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full bg-raised ring-1 ring-inset ring-hairline" aria-hidden="true">
                  <div className="absolute inset-y-0 rounded-full bg-leaf/25" style={{ left: `${(WINDOW[0] / 23) * 100}%`, right: `${100 - (WINDOW[1] / 23) * 100}%` }} />
                </div>
                <input
                  id="hour"
                  type="range"
                  min={0}
                  max={23}
                  step={1}
                  value={req.local_hour}
                  onChange={(e) => set("local_hour", Number(e.target.value))}
                  className="range relative"
                  aria-valuetext={`${pad(req.local_hour)}, ${outsideHours ? "outside" : "inside"} the 09:00 to 20:00 window`}
                />
              </div>
              <div className="flex justify-between font-mono text-2xs text-muted" aria-hidden="true">
                {[0, 3, 6, 9, 12, 15, 18, 21].map((h) => (
                  <span key={h} className={h >= WINDOW[0] && h <= WINDOW[1] ? "text-leaf" : ""}>
                    {h < 10 ? `0${h}` : h}
                  </span>
                ))}
              </div>
              <p className="mt-1 text-2xs text-muted">Green is the 09:00–20:00 window the policy permits. The demo sits at 23:00.</p>
            </div>

            {/* confidence slider with the threshold tick */}
            <div>
              <div className="flex items-baseline justify-between">
                <label htmlFor="conf" className="text-xs font-medium text-ink-2">
                  Resolution confidence
                </label>
                <span className={`font-mono text-sm font-semibold tnum ${aboveThreshold ? "text-ink-2" : "text-saffron"}`}>
                  {req.confidence.toFixed(2)} · {aboveThreshold ? "nothing to ask" : "would ask"}
                </span>
              </div>
              <div className="relative mt-1">
                <div className="pointer-events-none absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full bg-raised ring-1 ring-inset ring-hairline" aria-hidden="true">
                  <div className="absolute inset-y-0 left-0 rounded-full bg-saffron/25" style={{ width: `${THRESHOLD * 100}%` }} />
                  <div className="absolute -top-1 h-[18px] w-0.5 bg-ink" style={{ left: `${THRESHOLD * 100}%` }} />
                </div>
                <input
                  id="conf"
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={req.confidence}
                  onChange={(e) => set("confidence", Number(e.target.value))}
                  className="range relative"
                  aria-valuetext={`${req.confidence.toFixed(2)}, ${aboveThreshold ? "at or above" : "below"} the 0.80 threshold`}
                />
              </div>
              <p className="mt-1 text-2xs text-muted">At 0.80 and above the address auto-resolves and nobody is contacted.</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="channel" className="text-xs font-medium text-ink-2">
                  Channel
                </label>
                <select id="channel" value={req.channel} onChange={(e) => set("channel", e.target.value as AuthorizeRequest["channel"])} className="field mt-1">
                  <option value="sms">SMS</option>
                  <option value="whatsapp">WhatsApp — not approved</option>
                </select>
              </div>
              <div>
                <label htmlFor="principal" className="text-xs font-medium text-ink-2">
                  Who is asking
                </label>
                <select id="principal" value={req.principal} onChange={(e) => set("principal", e.target.value as AuthorizeRequest["principal"])} className="field mt-1">
                  <option value="clarifier">Clarifier agent</option>
                  <option value="operator">Human operator</option>
                </select>
              </div>
              <div>
                <label htmlFor="sent" className="text-xs font-medium text-ink-2">
                  Messages already sent
                </label>
                <select id="sent" value={req.messages_sent_for_order} onChange={(e) => set("messages_sent_for_order", Number(e.target.value))} className="field mt-1">
                  <option value={0}>0</option>
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                </select>
              </div>
              <div>
                <span className="text-xs font-medium text-ink-2">Customer opted out</span>
                <label htmlFor="opt" className="mt-1 flex h-[34px] cursor-pointer items-center gap-2 rounded-md border border-hairline-2 px-2.5 text-sm">
                  <input id="opt" type="checkbox" checked={req.opted_out} onChange={(e) => set("opted_out", e.target.checked)} className="h-4 w-4 accent-deny" />
                  <span>Yes — never contact</span>
                </label>
              </div>
            </div>

            <div className="flex items-center gap-3">
              <button type="submit" disabled={busy || !req.order_id.trim()} className="btn w-full sm:w-auto">
                <Icon name="shield" />
                {busy ? "Asking Cedar…" : "May we contact the customer?"}
              </button>
              <span className="hidden text-xs text-muted sm:inline">⌘/Ctrl + Enter</span>
            </div>
            {error && <ErrorNote>{error}</ErrorNote>}
          </form>
        </Panel>

        {/* ------------------------------------------------ decision + policy */}
        <div className="space-y-4">
          <div aria-live="polite" aria-atomic="true">
            <DecisionPanel decision={decision} busy={busy} stale={stale} />
          </div>
          <Panel title="policies/contact.cedar" aside="what the engine read · highlighted: the deciding clause" padded={false}>
            <div className="p-2">
              <CedarViewer clause={busy ? null : clause} tone={decision && !stale ? (decision.allowed ? "allow" : "deny") : null} />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function DecisionPanel({ decision, busy, stale }: { decision: AuthzDecision | null; busy: boolean; stale: boolean }) {
  if (busy) {
    return (
      <section className="panel p-5" aria-busy="true">
        <div className="panel-title">Decision</div>
        <div className="bone mt-3 h-10 w-32" />
        <div className="bone mt-3 h-3.5 w-3/4" />
        <div className="mt-3 flex gap-2">
          <div className="bone h-5 w-40 rounded-full" />
        </div>
      </section>
    );
  }
  if (!decision) {
    return (
      <section className="panel p-5">
        <div className="panel-title">Decision</div>
        <div className="mt-3">
          <Empty icon="shield" title="No decision yet" hint="Set the clock and the confidence, then ask. The answer names the policy and the clause that decided it." />
        </div>
      </section>
    );
  }
  const allow = decision.allowed;
  return (
    <section className={`panel p-5 transition-[border-color,box-shadow] duration-slow ease-out ${stale ? "" : allow ? "verdict-allow" : "verdict-deny"}`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="panel-title">Decision{stale && <span className="ml-2 normal-case tracking-normal text-warning">· inputs changed — ask again</span>}</div>
          <div className={`mt-1 font-mono text-3xl font-bold tracking-tight ${stale ? "text-muted" : allow ? "text-leaf" : "text-deny"}`}>{decision.decision}</div>
        </div>
        <div className="shrink-0 text-right font-mono text-2xs text-muted">
          <div>{decision.principal}</div>
          <div>{decision.action}</div>
        </div>
      </div>

      <p className="mt-3 text-base text-ink">{decision.reason}</p>

      <div className="mt-4">
        <div className="panel-title">Deciding policy</div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {decision.matched_policies.length ? (
            decision.matched_policies.map((p) => (
              <span key={p} className={`pill ${allow ? "border-leaf/40 bg-leaf/10 text-leaf" : "border-deny/40 bg-deny/10 text-deny"}`}>
                @id("{p}")
              </span>
            ))
          ) : (
            <span className="pill border-hairline-2 bg-raised text-ink-2">no permit applied — Cedar denies by default</span>
          )}
        </div>
      </div>

      {!allow && (
        <p className="mt-4 flex items-start gap-2 rounded-md border border-hairline bg-raised px-3 py-2 text-sm text-ink-2">
          <Icon name="arrow" className="mt-0.5 h-4 w-4 shrink-0 text-saffron" />
          <span>
            Nothing was sent. The case is now in the{" "}
            <Link to="/queue" className="font-semibold text-saffron underline underline-offset-2">
              review queue
            </Link>{" "}
            with this reason — a denial is never a silent drop.
          </span>
        </p>
      )}

      {decision.errors.length > 0 && <div className="mt-3"><ErrorNote>Evaluator error, failed closed: {decision.errors.join("; ")}</ErrorNote></div>}

      <details className="mt-4 text-xs">
        <summary className="cursor-pointer text-muted">Context the engine saw</summary>
        <pre className="mt-2 overflow-x-auto rounded-md bg-raised p-2 font-mono text-2xs text-ink-2">{JSON.stringify(decision.context, null, 2)}</pre>
      </details>
    </section>
  );
}
