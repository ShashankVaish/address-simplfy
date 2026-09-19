/**
 * Guardrails: ask Cedar whether we may contact a customer, right now.
 *
 * This is the demo beat at 1:45 -- clock at 23:00, DENY on screen, case in
 * the review queue. The controls are the policy's inputs, nothing more, so
 * what the operator sees is exactly what the policy engine saw. The decision
 * card shows the matched policy ids because a denial without a policy name is
 * an accusation without a charge.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, authorize, isMock } from "../api/client";
import type { AuthorizeRequest, AuthzDecision } from "../api/types";
import { Card } from "../components/ui";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

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

export default function Guardrails() {
  const [req, setReq] = useState<AuthorizeRequest>(DEFAULT);
  const [decision, setDecision] = useState<AuthzDecision | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof AuthorizeRequest>(k: K, v: AuthorizeRequest[K]) => {
    setReq((r) => ({ ...r, [k]: v }));
    setDecision(null);
  };

  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      setDecision(await authorize(req));
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <div>
          <h1 className="text-lg font-semibold">Guardrails</h1>
          <p className="text-sm text-gray-500">
            Every outbound message is authorised by Cedar first. Policy is a text file; a denial names the policy.
          </p>
        </div>
        {isMock && <span className="text-xs text-gray-400">mock — same rules, evaluated locally</span>}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_1.2fr]">
        <Card title="The situation">
          <div className="space-y-4 text-sm">
            <Field label="Order">
              <input
                value={req.order_id}
                onChange={(e) => set("order_id", e.target.value)}
                className="w-full rounded border border-gray-200 px-2 py-1 font-mono text-xs focus:border-saffron focus:outline-none"
              />
            </Field>

            <Field label={`Customer's local time — ${String(req.local_hour).padStart(2, "0")}:00`}>
              <input
                type="range"
                min={0}
                max={23}
                value={req.local_hour}
                onChange={(e) => set("local_hour", Number(e.target.value))}
                className="w-full accent-saffron"
                aria-label="local hour"
              />
              <div className="mt-1 flex justify-between text-[10px] text-gray-400">
                {HOURS.filter((h) => h % 3 === 0).map((h) => (
                  <span key={h} className={h >= 9 && h <= 20 ? "text-leaf" : ""}>
                    {String(h).padStart(2, "0")}
                  </span>
                ))}
              </div>
              <p className="mt-1 text-[11px] text-gray-500">Contact window 09:00–20:00 is green. Drag to 23:00 for the demo.</p>
            </Field>

            <Field label={`Resolution confidence — ${req.confidence.toFixed(2)}`}>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={req.confidence}
                onChange={(e) => set("confidence", Number(e.target.value))}
                className="w-full accent-saffron"
                aria-label="confidence"
              />
              <p className="mt-1 text-[11px] text-gray-500">At 0.80 and above we do not bother the customer at all.</p>
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Channel">
                <select value={req.channel} onChange={(e) => set("channel", e.target.value as AuthorizeRequest["channel"])} className={selectCls}>
                  <option value="sms">SMS</option>
                  <option value="whatsapp">WhatsApp (not approved)</option>
                </select>
              </Field>
              <Field label="Who is asking">
                <select value={req.principal} onChange={(e) => set("principal", e.target.value as AuthorizeRequest["principal"])} className={selectCls}>
                  <option value="clarifier">Clarifier agent</option>
                  <option value="operator">Human operator</option>
                </select>
              </Field>
              <Field label="Messages already sent">
                <select value={req.messages_sent_for_order} onChange={(e) => set("messages_sent_for_order", Number(e.target.value))} className={selectCls}>
                  <option value={0}>0</option>
                  <option value={1}>1</option>
                  <option value={2}>2</option>
                </select>
              </Field>
              <Field label="Customer opted out">
                <label className="flex items-center gap-2 pt-1">
                  <input type="checkbox" checked={req.opted_out} onChange={(e) => set("opted_out", e.target.checked)} className="accent-saffron" />
                  <span className="text-gray-700">Yes — never contact</span>
                </label>
              </Field>
            </div>

            <button
              onClick={() => void ask()}
              disabled={busy || !req.order_id}
              className="w-full rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:opacity-50"
            >
              {busy ? "Asking Cedar…" : "May we contact the customer?"}
            </button>
            {error && <p className="rounded-lg bg-red-50 p-2 text-xs text-red-700">{error}</p>}
          </div>
        </Card>

        <div className="space-y-4">
          <DecisionCard decision={decision} />
          <Card title="policies/contact.cedar — what the engine read">
            <pre className="max-h-72 overflow-auto rounded-lg bg-gray-950 p-3 text-[11px] leading-relaxed text-gray-100">{POLICY_TEXT}</pre>
          </Card>
        </div>
      </div>
    </div>
  );
}

function DecisionCard({ decision }: { decision: AuthzDecision | null }) {
  if (!decision) {
    return (
      <Card title="Decision">
        <p className="text-sm text-gray-500">No decision yet. Set the clock, then ask.</p>
      </Card>
    );
  }
  const allowed = decision.allowed;
  return (
    <Card title="Decision" className={allowed ? "ring-leaf/40" : "ring-red-300"}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className={`text-3xl font-black tracking-tight ${allowed ? "text-leaf" : "text-red-600"}`}>{decision.decision}</div>
          <p className="mt-1 text-sm text-gray-800">{decision.reason}</p>
        </div>
        <div className="shrink-0 text-right text-[11px] text-gray-500">
          <div>{decision.principal}</div>
          <div>{decision.action}</div>
        </div>
      </div>

      <div className="mt-3">
        <div className="text-[11px] uppercase tracking-wide text-gray-500">Matched policies</div>
        <div className="mt-1 flex flex-wrap gap-1.5">
          {decision.matched_policies.length ? (
            decision.matched_policies.map((p) => (
              <span key={p} className={`rounded-full px-2.5 py-0.5 font-mono text-xs ring-1 ${allowed ? "bg-leaf/10 text-leaf ring-leaf/30" : "bg-red-50 text-red-700 ring-red-200"}`}>
                @id("{p}")
              </span>
            ))
          ) : (
            <span className="text-xs text-gray-500">none — no permit applied, so Cedar denies by default</span>
          )}
        </div>
      </div>

      {!allowed && (
        <p className="mt-3 rounded-lg bg-saffron/10 p-2 text-xs text-gray-800">
          Nothing was sent. The case is now in the{" "}
          <Link to="/queue" className="font-semibold text-saffron underline">
            review queue
          </Link>{" "}
          with this policy id and reason — a denial is never a silent drop.
        </p>
      )}

      {decision.errors.length > 0 && (
        <p className="mt-3 rounded-lg bg-red-50 p-2 text-xs text-red-700">Evaluator error, failed closed: {decision.errors.join("; ")}</p>
      )}

      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-gray-500">Context the engine saw</summary>
        <pre className="mt-1 overflow-auto rounded bg-gray-50 p-2 font-mono text-[11px]">{JSON.stringify(decision.context, null, 2)}</pre>
      </details>
    </Card>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-gray-600">{label}</div>
      {children}
    </div>
  );
}

const selectCls = "w-full rounded border border-gray-200 bg-white px-2 py-1 text-xs focus:border-saffron focus:outline-none";

// A copy of backend/layers/common/python/patasetu/policies/contact.cedar for
// the screen. The backend evaluates the file, not this string.
const POLICY_TEXT = `@id("contact-allowed-window")
permit(
  principal == Agent::"clarifier",
  action == Action::"contactCustomer",
  resource in OrderGroup::"pending_resolution"
) when {
  context.confidence_pct < 80 &&
  context.messages_sent_for_order == 0 &&
  context.local_hour >= 9 && context.local_hour <= 20 &&
  context.channel == "sms"
};

// A forbid beats every permit. No principal named: nobody overrides an opt-out.
@id("contact-opted-out")
forbid(
  principal,
  action == Action::"contactCustomer",
  resource in OrderGroup::"opted_out"
);

// A person exercising judgement is not bound by the agent's quiet hours.
@id("contact-human-operator")
permit(
  principal == Agent::"operator",
  action == Action::"contactCustomer",
  resource in OrderGroup::"pending_resolution"
);`;
