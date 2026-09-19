/**
 * The Playground: paste a messy address, watch it resolve.
 *
 * The stage-by-stage reveal is deliberate theatre with a real basis: each
 * group appears in the order the pipeline produces it -- deterministic fields,
 * then landmarks, then the pin, then DIGIPIN and the verdict -- and the rail
 * above the results lights the same stages with their real milliseconds. A
 * cached answer skips the theatre: it is instant and should look it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, isMock, resolve } from "../api/client";
import type { Resolution } from "../api/types";
import { ConfidenceGauge, DigipinPlate, PipelineRail } from "../components/instruments";
import { Empty, ErrorNote, EvidenceList, Icon, LandmarkList, MapPanel, Panel, RecordSkeleton, StatusPill, StructuredRecord } from "../components/ui";

// The first two are places the deployed landmark graph knows (seeded from the
// pincode directory), so they resolve from the graph. The Ramesh Nagar one is
// the README's running example: its landmarks are NOT in the graph, which
// shows the honest fallback path -- geocoder, lower confidence, one question.
const EXAMPLES: { text: string; note: string }[] = [
  { text: "h no 14 near okhla industrial estate opp greater kailash kalkaji new delhi 110019 call before coming 9876543210", note: "graph hit · phone stripped" },
  { text: "flat 12 prem nagar near mangolpuri n block opp rohini sector 23 delhi 110086", note: "graph hit" },
  { text: "शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015", note: "Devanagari · asks one question" },
  { text: "h no 14 behind shiv mandir near gupta general store opp water tank ramesh nagar delhi 110015 call before coming", note: "landmarks not in graph yet" },
  { text: "Baulabanadh Post Office, near Nairi, Bhubaneswar - 752034", note: "Odisha" },
];

const DETERMINISTIC = new Set(["pincode", "state", "district", "city", "locality", "sub_locality", "building", "street"]);

type Stage = 0 | 1 | 2 | 3 | 4;
// 0 nothing · 1 fields · 2 landmarks · 3 pin · 4 digipin + verdict
// Which pipeline stage index (S0..S7) the rail lights through at each reveal stage.
const RAIL_THROUGH: Record<Stage, number> = { 0: -1, 1: 1, 2: 2, 3: 4, 4: 7 };

/**
 * What the deterministic parser will see, previewed client-side as you type.
 * Only the obvious tokens -- pincode, house number, relation words, phone --
 * and labelled as a preview: the backend's S1 is the source of truth.
 */
function preview(raw: string) {
  const pincode = raw.match(/\b[1-8]\d{5}\b/)?.[0] ?? null;
  const house = raw.match(/\b(?:h\s*\.?\s*no\.?|house\s*no\.?|flat(?:\s*no\.?)?|plot(?:\s*no\.?)?|h\.?\s*n\.?)\s*[:\-#]?\s*([A-Za-z0-9/-]{1,8})/i)?.[1] ?? null;
  const phones = (raw.match(/(?<!\d)[6-9]\d{9}(?!\d)/g) ?? []).length;
  const relations = Array.from(raw.toLowerCase().matchAll(/\b(near|behind|opp(?:osite)?|beside|above|inside|next to|in front of)\b|(के पीछे|के पास|के सामने|के ऊपर|के बगल)/g)).map((m) => m[1] ?? m[2]);
  const devanagari = /[ऀ-ॿ]/.test(raw);
  return { pincode, house, phones, relations, devanagari };
}

export default function Playground({ theme }: { theme: "light" | "dark" }) {
  const [raw, setRaw] = useState(EXAMPLES[0].text);
  const [result, setResult] = useState<Resolution | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<Stage>(0);

  const run = useCallback(async () => {
    if (!raw.trim() || busy) return;
    setBusy(true);
    setError(null);
    setStage(0);
    try {
      const r = await resolve({ raw: raw.trim() });
      setResult(r);
    } catch (e) {
      setResult(null);
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e));
    } finally {
      setBusy(false);
    }
  }, [raw, busy]);

  useEffect(() => {
    if (!result) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (result.cached || reduce) {
      setStage(4);
      return;
    }
    const timers = [1, 2, 3, 4].map((s, i) => setTimeout(() => setStage(s as Stage), 200 + i * 420));
    return () => timers.forEach(clearTimeout);
  }, [result]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run]);

  const calibrated = useMemo(() => !(result?.evidence ?? []).some((e) => e.includes("uncalibrated")), [result]);
  const totalMs = useMemo(() => Object.values(result?.timings_ms ?? {}).reduce((a, b) => a + b, 0), [result]);
  const pv = useMemo(() => preview(raw), [raw]);

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------------ headline */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="eyebrow">S0 → S7 · one request</div>
          <h1 className="mt-1 text-xl font-extrabold tracking-tight">Playground</h1>
          <p className="mt-1 max-w-[62ch] text-sm text-ink-2">Paste an address the way a customer wrote it. Watch it become fields, landmarks, a doorstep and a DIGIPIN — with the evidence for each.</p>
        </div>
        {isMock && (
          <span className="pill border-saffron/40 bg-saffron/10 text-saffron" title="VITE_API_URL is unset">
            mock mode
          </span>
        )}
      </div>

      {/* ------------------------------------------------------------ rail */}
      <PipelineRail busy={busy} timings={result?.timings_ms ?? null} litThrough={RAIL_THROUGH[stage]} cached={result?.cached ?? false} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        {/* ---------------------------------------------------------- input + verdict */}
        <div className="space-y-4">
          <Panel title="Address" aside={pv.devanagari ? "Devanagari · the question will be in Hindi" : "Hindi, English or both"}>
            <label htmlFor="raw" className="sr-only">
              Address text
            </label>
            <textarea
              id="raw"
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              rows={4}
              spellCheck={false}
              className={`field resize-none font-mono text-[15px] leading-relaxed transition duration-base ease-out ${busy ? "border-saffron ring-2 ring-saffron/25" : ""}`}
              placeholder="Paste a messy address exactly as the customer wrote it…"
            />

            {/* live parse preview */}
            <div className="mt-2 flex flex-wrap items-center gap-1.5" aria-live="polite" aria-label="What the parser can already see">
              <span className="mr-1 font-mono text-2xs uppercase tracking-[0.08em] text-muted">S1 preview</span>
              {pv.pincode ? <Tok tone="leaf" k="pincode" v={pv.pincode} /> : <Tok tone="muted" k="pincode" v="none" />}
              {pv.house && <Tok tone="leaf" k="house" v={pv.house} />}
              {pv.relations.map((r, i) => (
                <Tok key={i} tone="saffron" k="relation" v={r} />
              ))}
              {pv.phones > 0 && <Tok tone="deny" k="phone" v={`${pv.phones} → stripped`} />}
              {!pv.house && !pv.relations.length && !pv.phones && pv.pincode && <span className="text-2xs text-muted">no house number or landmark phrase</span>}
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
              <button type="button" onClick={() => void run()} disabled={busy || !raw.trim()} className="btn">
                <Icon name="spark" />
                {busy ? "Resolving…" : "Resolve"}
              </button>
              <span className="text-xs text-muted">⌘/Ctrl + Enter</span>
            </div>

            <div className="mt-4">
              <div className="panel-title mb-2">Try one</div>
              <div className="flex flex-col gap-1.5">
                {EXAMPLES.map((ex) => {
                  const active = ex.text === raw;
                  return (
                    <button
                      key={ex.text}
                      type="button"
                      onClick={() => setRaw(ex.text)}
                      aria-pressed={active}
                      className={`grid grid-cols-[1fr_auto] items-center gap-3 rounded-md border px-2.5 py-1.5 text-left transition duration-fast ease-out ${
                        active ? "border-saffron bg-saffron/5" : "border-hairline bg-raised hover:border-hairline-2 hover:-translate-y-px"
                      }`}
                      title={ex.text}
                    >
                      <span className="truncate font-mono text-xs text-ink">{ex.text}</span>
                      <span className="font-mono text-2xs text-muted">{ex.note}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            {error && (
              <div className="mt-3">
                <ErrorNote>{error}</ErrorNote>
              </div>
            )}
          </Panel>

          <div aria-live="polite" aria-atomic="true">
            {busy && !result && (
              <section className="panel p-4" aria-busy="true">
                <div className="panel-title">Verdict</div>
                <div className="mt-3 flex items-center justify-between">
                  <div className="bone h-5 w-28 rounded-full" />
                  <div className="bone h-3 w-16" />
                </div>
                <div className="bone mx-auto mt-4 h-24 w-48 rounded-t-full" />
              </section>
            )}
            {result && (
              <Panel
                title="Verdict"
                aside={
                  <span className="font-mono tnum">
                    {result.cached ? "from cache · " : ""}
                    {totalMs.toFixed(0)} ms
                  </span>
                }
                className={`reveal ${stage >= 4 ? "" : "reveal-hidden"}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <span className={stage >= 4 ? "stamp" : ""}>
                    <StatusPill status={result.status} />
                  </span>
                  {result.correlation_id && (
                    <span className="truncate font-mono text-2xs text-muted" title="correlation id">
                      {result.correlation_id}
                    </span>
                  )}
                </div>
                <div className="mt-2">
                  <ConfidenceGauge value={stage >= 4 ? result.confidence : 0} calibrated={calibrated} />
                </div>
                {result.clarification && (
                  <div className="mt-4 rounded-md border border-saffron/40 bg-saffron/5 p-3">
                    <div className="eyebrow">
                      One question · {result.clarification.language} · about "{result.clarification.field}"
                    </div>
                    <p className="mt-1.5 text-md font-medium text-ink">{result.clarification.question}</p>
                    <p className="mt-1 text-2xs text-muted">Sent only if Cedar allows contact right now — see Guardrails.</p>
                  </div>
                )}
                {result.alternatives.length > 0 && (
                  <div className="mt-4">
                    <div className="panel-title">Two readings — not guessing</div>
                    <ul className="mt-2 divide-y divide-hairline rounded-md border border-hairline">
                      {result.alternatives.map((alt, i) => (
                        <li key={i} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs">
                          <span className="font-medium">{alt.reason}</span>
                          <span className="font-mono text-muted tnum">
                            {alt.confidence.toFixed(2)}
                            {alt.geo ? ` · ${alt.geo.lat.toFixed(4)}, ${alt.geo.lng.toFixed(4)}` : ""}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </Panel>
            )}
          </div>
        </div>

        {/* ---------------------------------------------------------- record + map */}
        <div className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="Structured" aside={result ? "stated · pincode · inferred" : "S1 + S3"}>
              {busy && !result ? (
                <RecordSkeleton rows={8} />
              ) : result ? (
                <StructuredRecord structured={result.structured} evidence={result.evidence} revealed={stage >= 1 ? DETERMINISTIC : new Set()} />
              ) : (
                <Empty icon="check" title="Eight fields, parsed" hint="Each value carries where it came from: stated in the text, follows from the pincode, or inferred by the model." />
              )}
            </Panel>
            <Panel title="Landmarks" aside={result ? "relation · name · graph" : "S2"}>
              {busy && !result ? (
                <RecordSkeleton rows={3} />
              ) : result ? (
                <LandmarkList landmarks={result.structured.landmarks} revealed={stage >= 2} />
              ) : (
                <Empty icon="graph" title="near · behind · opposite" hint="The relation word is the product: 'behind Shiv Mandir' and 'opposite Shiv Mandir' are different doorsteps." />
              )}
            </Panel>
          </div>

          <Panel title="Location" aside={result?.geo ? <span className="font-mono tnum">{result.geo.lat.toFixed(5)}, {result.geo.lng.toFixed(5)}</span> : "S4 + S5"} padded={false}>
            <div className="p-3">
              <MapPanel geo={stage >= 3 ? (result?.geo ?? null) : null} theme={theme} dim={busy || (!!result && stage < 3)} />
            </div>
            <div className={`reveal flex flex-wrap items-center justify-between gap-3 border-t border-hairline px-4 py-3 ${stage >= 4 && result ? "" : "reveal-hidden"}`}>
              {result?.digipin ? (
                <DigipinPlate digipin={result.digipin} />
              ) : result ? (
                <Empty icon="pin" title="No DIGIPIN" hint="A DIGIPIN needs a coordinate; this address did not get one." />
              ) : (
                <span className="text-xs text-muted">The DIGIPIN plate appears here</span>
              )}
            </div>
          </Panel>

          {result && (
            <Panel title="Evidence" aside="what an analyst reads before trusting the answer" className={`reveal ${stage >= 4 ? "" : "reveal-hidden"}`}>
              <EvidenceList evidence={result.evidence} />
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

function Tok({ tone, k, v }: { tone: "leaf" | "saffron" | "deny" | "muted"; k: string; v: string }) {
  const cls = { leaf: "border-leaf/40 bg-leaf/10 text-leaf", saffron: "border-saffron/40 bg-saffron/10 text-saffron", deny: "border-deny/40 bg-deny/10 text-deny", muted: "border-hairline-2 bg-raised text-muted" }[tone];
  return (
    <span className={`pill ${cls}`}>
      <span className="opacity-70">{k}</span>
      <span>{v}</span>
    </span>
  );
}
