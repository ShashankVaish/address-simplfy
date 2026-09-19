/**
 * The Playground: paste a messy address, watch it resolve.
 *
 * The stage-by-stage reveal is deliberate theatre with a real basis: each
 * group appears in the order the pipeline produces it -- deterministic fields,
 * then landmarks, then the pin, then DIGIPIN and the verdict. A cached answer
 * skips the theatre: it is instant and should look it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, isMock, resolve } from "../api/client";
import type { Resolution } from "../api/types";
import {
  ConfidenceMeter,
  DigipinBadge,
  Empty,
  ErrorNote,
  EvidenceList,
  Icon,
  LandmarkList,
  MapPanel,
  Panel,
  RecordSkeleton,
  StatusPill,
  StructuredRecord,
} from "../components/ui";

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
  const isDevanagari = /[ऀ-ॿ]/.test(raw);

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      {/* ---------------------------------------------------------- input + verdict */}
      <div className="space-y-4">
        <Panel title="Address" aside={isDevanagari ? "Devanagari detected · answer will be in Hindi" : "Hindi, English or both"}>
          <label htmlFor="raw" className="sr-only">
            Address text
          </label>
          <textarea
            id="raw"
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            rows={4}
            spellCheck={false}
            className={`field resize-none font-mono text-sm leading-relaxed transition duration-base ease-out ${busy ? "border-saffron ring-2 ring-saffron/25" : ""}`}
            placeholder="Paste a messy address exactly as the customer wrote it…"
          />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button type="button" onClick={() => void run()} disabled={busy || !raw.trim()} className="btn">
              <Icon name="spark" />
              {busy ? "Resolving…" : "Resolve"}
            </button>
            <span className="text-xs text-muted">⌘/Ctrl + Enter</span>
            {isMock && (
              <span className="pill ml-auto border-saffron/40 bg-saffron/10 text-saffron" title="VITE_API_URL is unset">
                mock mode
              </span>
            )}
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
                      active ? "border-saffron bg-saffron/5" : "border-hairline bg-raised hover:border-hairline-2"
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
              <div className="bone mt-4 h-2.5 w-full rounded-full" />
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
                <StatusPill status={result.status} />
                {result.correlation_id && (
                  <span className="truncate font-mono text-2xs text-muted" title="correlation id">
                    {result.correlation_id}
                  </span>
                )}
              </div>
              <div className="mt-4">
                <ConfidenceMeter value={result.confidence} calibrated={calibrated} />
              </div>
              {result.clarification && (
                <div className="mt-4 rounded-md border border-saffron/40 bg-saffron/5 p-3">
                  <div className="eyebrow">
                    One question · {result.clarification.language} · about "{result.clarification.field}"
                  </div>
                  <p className="mt-1.5 text-base font-medium text-ink">{result.clarification.question}</p>
                  <p className="mt-1 text-2xs text-muted">Sent only if Cedar allows contact right now. See Guardrails.</p>
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
          <Panel title="Structured" aside={result ? "stated · pincode · inferred" : undefined}>
            {busy && !result ? (
              <RecordSkeleton rows={8} />
            ) : result ? (
              <StructuredRecord structured={result.structured} evidence={result.evidence} revealed={stage >= 1 ? DETERMINISTIC : new Set()} />
            ) : (
              <Empty icon="check" title="Eight fields, parsed" hint="Building, street, locality… with a marker for what was stated, what follows from the pincode, and what the model inferred." />
            )}
          </Panel>
          <Panel title="Landmarks" aside={result ? "relation · name · graph" : undefined}>
            {busy && !result ? (
              <RecordSkeleton rows={3} />
            ) : result ? (
              <LandmarkList landmarks={result.structured.landmarks} revealed={stage >= 2} />
            ) : (
              <Empty icon="graph" title="near · behind · opposite" hint="The relation word is the product: 'behind Shiv Mandir' and 'opposite Shiv Mandir' are different doorsteps." />
            )}
          </Panel>
        </div>

        <Panel title="Location" aside={result?.geo ? `${result.geo.lat.toFixed(5)}, ${result.geo.lng.toFixed(5)}` : undefined} padded={false}>
          <div className="p-3">
            <MapPanel geo={stage >= 3 ? (result?.geo ?? null) : null} theme={theme} dim={busy || (!!result && stage < 3)} />
          </div>
          <div className={`reveal border-t border-hairline px-4 py-3 ${stage >= 4 && result ? "" : "reveal-hidden"}`}>
            {result ? <DigipinBadge digipin={result.digipin} /> : <span className="text-xs text-muted">DIGIPIN appears here</span>}
          </div>
        </Panel>

        {result && (
          <Panel title="Evidence" aside="what an analyst reads before trusting the answer" className={`reveal ${stage >= 4 ? "" : "reveal-hidden"}`}>
            <EvidenceList evidence={result.evidence} />
          </Panel>
        )}
      </div>
    </div>
  );
}
