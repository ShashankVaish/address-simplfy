/**
 * Read-only viewer for policies/contact.cedar with a tokeniser small enough to
 * read in one sitting. Cedar's surface grammar here is: comments, keywords,
 * annotations, entity types, strings, numbers, operators and identifiers.
 *
 * The viewer can light up the clause that decided the current answer, so the
 * verdict and its proof sit on the same screen.
 */

import type { AuthzDecision } from "../api/types";

// A copy of backend/layers/common/python/patasetu/policies/contact.cedar.
// The backend evaluates the file, not this string; keep them in step.
export const CONTACT_CEDAR = `// Contacting a customer is permitted only under ALL of these conditions.
@id("contact-allowed-window")
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

// Never, at any confidence, at any hour, by ANY principal, for an opted-out
// customer. A forbid beats every permit. No principal named on purpose.
@id("contact-opted-out")
forbid(
  principal,
  action == Action::"contactCustomer",
  resource in OrderGroup::"opted_out"
);

// A human operator may always reach out; quiet hours bind the agent, not a
// person exercising judgement.
@id("contact-human-operator")
permit(
  principal == Agent::"operator",
  action == Action::"contactCustomer",
  resource in OrderGroup::"pending_resolution"
);`;

export type Clause = "hours" | "confidence" | "messages" | "channel" | "window" | "opted-out" | "operator" | null;

/** Which lines (1-based) each clause occupies in CONTACT_CEDAR. */
const CLAUSE_LINES: Record<Exclude<Clause, null>, [number, number]> = {
  confidence: [8, 8],
  messages: [9, 9],
  hours: [10, 10],
  channel: [11, 11],
  window: [2, 12],
  "opted-out": [16, 21],
  operator: [25, 30],
};

/** Map a decision back to the clause that produced it. */
export function clauseFor(d: AuthzDecision | null): Clause {
  if (!d) return null;
  if (d.matched_policies.includes("contact-opted-out")) return "opted-out";
  if (d.matched_policies.includes("contact-human-operator")) return "operator";
  if (d.matched_policies.includes("contact-allowed-window")) return "window";
  const r = d.reason.toLowerCase();
  if (r.includes("outside contact hours")) return "hours";
  if (r.includes("already been sent") || r.includes("one per order")) return "messages";
  if (r.includes("threshold")) return "confidence";
  if (r.includes("channel")) return "channel";
  return null;
}

type Tok = { t: "cm" | "kw" | "an" | "ty" | "st" | "nu" | "op" | "id" | "ws"; v: string };

const KEYWORDS = new Set(["permit", "forbid", "when", "unless", "principal", "action", "resource", "context", "in", "has", "like", "if", "then", "else", "true", "false"]);

function tokenize(line: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < line.length) {
    const rest = line.slice(i);
    let m: RegExpMatchArray | null;
    if ((m = rest.match(/^\/\/.*$/))) out.push({ t: "cm", v: m[0] });
    else if ((m = rest.match(/^\s+/))) out.push({ t: "ws", v: m[0] });
    else if ((m = rest.match(/^@[A-Za-z_]+/))) out.push({ t: "an", v: m[0] });
    else if ((m = rest.match(/^"(?:[^"\\]|\\.)*"/))) out.push({ t: "st", v: m[0] });
    else if ((m = rest.match(/^\d+/))) out.push({ t: "nu", v: m[0] });
    else if ((m = rest.match(/^[A-Z][A-Za-z]*::/))) out.push({ t: "ty", v: m[0] });
    else if ((m = rest.match(/^[A-Za-z_][A-Za-z0-9_]*/))) out.push({ t: KEYWORDS.has(m[0]) ? "kw" : "id", v: m[0] });
    else if ((m = rest.match(/^(==|!=|<=|>=|&&|\|\||[<>(){};,.])/))) out.push({ t: "op", v: m[0] });
    else out.push({ t: "id", v: rest[0] });
    i += (m ? m[0] : rest[0]).length;
  }
  return out;
}

const TOK_CLASS: Record<Tok["t"], string> = {
  cm: "text-code-dim italic",
  kw: "text-code-kw",
  an: "text-saffron",
  ty: "text-code-ty",
  st: "text-code-st",
  nu: "text-code-nu",
  op: "text-code-dim",
  id: "text-code-ink",
  ws: "",
};

export function CedarViewer({ source = CONTACT_CEDAR, clause, tone }: { source?: string; clause: Clause; tone: "allow" | "deny" | null }) {
  const lines = source.split("\n");
  const range = clause ? CLAUSE_LINES[clause] : null;
  const hl = tone === "allow" ? "bg-leaf/15" : tone === "deny" ? "bg-deny/15" : "";
  const bar = tone === "allow" ? "bg-leaf" : tone === "deny" ? "bg-deny" : "";
  return (
    <div className="max-w-full overflow-x-auto rounded-md bg-sunken font-mono text-[11.5px] leading-[1.6] text-code-ink">
      <pre className="m-0 min-w-max p-3">
        {lines.map((line, i) => {
          const n = i + 1;
          const on = range !== null && n >= range[0] && n <= range[1];
          return (
            <div key={n} className={`grid grid-cols-[3px_2.25rem_1fr] ${on ? hl : ""}`}>
              <span className={on ? bar : ""} aria-hidden="true" />
              <span className="select-none pr-3 text-right text-code-dim tnum">{n}</span>
              <span>
                {tokenize(line).map((tok, j) => (
                  <span key={j} className={TOK_CLASS[tok.t]}>
                    {tok.v}
                  </span>
                ))}
              </span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}
