/**
 * The Playground: paste a messy address, watch it resolve.
 *
 * The stage-by-stage reveal is deliberate theatre with a real basis: each
 * group of fields appears in the order the pipeline actually produces them --
 * deterministic fields first, then landmarks, then the pin, then the DIGIPIN.
 * It is the most persuasive twenty seconds available and the video opens here.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, isMock, resolve } from "../api/client";
import type { Resolution } from "../api/types";
import { Card, ConfidenceBar, DigipinBadge, EvidenceList, LandmarkList, MapPin, StatusPill, StructuredPanel } from "../components/ui";

const EXAMPLES = [
  "h no 14 behind shiv mandir near gupta general store opp water tank ramesh nagar delhi 110015 call before coming",
  "शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015",
  "Flat 4B, Sunrise Apts, opp SBI, Sec 22, Noida, UP 201301",
  "Baulabanadh Post Office, near Nairi, Bhubaneswar - 752034",
];

const DETERMINISTIC = new Set(["pincode", "state", "district", "city", "locality", "sub_locality", "building", "street"]);

type Stage = 0 | 1 | 2 | 3 | 4;
// 0 nothing · 1 fields · 2 landmarks · 3 pin · 4 digipin + verdict

export default function Playground() {
  const [raw, setRaw] = useState(EXAMPLES[0]);
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

  // Reveal in pipeline order once a result lands. Cached answers skip the
  // theatre: they are instant and should look it.
  useEffect(() => {
    if (!result) return;
    if (result.cached) {
      setStage(4);
      return;
    }
    const timers = [1, 2, 3, 4].map((s, i) => setTimeout(() => setStage(s as Stage), 250 + i * 450));
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

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <div className="space-y-4">
        <Card title="Address">
          <textarea
            value={raw}
            onChange={(e) => setRaw(e.target.value)}
            rows={4}
            spellCheck={false}
            className="w-full resize-none rounded-lg border border-gray-300 p-3 font-mono text-sm focus:border-saffron focus:outline-none focus:ring-2 focus:ring-saffron/30"
            placeholder="Paste a messy address…"
          />
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={() => void run()}
              disabled={busy || !raw.trim()}
              className="rounded-lg bg-ink px-4 py-2 text-sm font-semibold text-white transition hover:bg-black disabled:opacity-40"
            >
              {busy ? "Resolving…" : "Resolve"}
            </button>
            <span className="text-xs text-gray-500">⌘/Ctrl + Enter</span>
            {isMock && <span className="ml-auto rounded bg-saffron/10 px-2 py-0.5 text-[11px] font-medium text-saffron">mock mode</span>}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((ex) => (
              <button key={ex} onClick={() => setRaw(ex)} className="truncate rounded-full bg-gray-100 px-3 py-1 text-[11px] text-gray-700 hover:bg-gray-200" style={{ maxWidth: "100%" }} title={ex}>
                {ex.length > 48 ? ex.slice(0, 48) + "…" : ex}
              </button>
            ))}
          </div>
          {error && <p className="mt-3 rounded-lg bg-red-50 p-2 text-sm text-red-700">{error}</p>}
        </Card>

        {result && (
          <Card title="Verdict" className={`transition-opacity duration-500 ${stage >= 4 ? "opacity-100" : "opacity-0"}`}>
            <div className="flex items-center justify-between">
              <StatusPill status={result.status} />
              <span className="text-xs text-gray-500">
                {result.cached ? "from cache · " : ""}
                {totalMs.toFixed(0)} ms
              </span>
            </div>
            <div className="mt-3">
              <ConfidenceBar value={result.confidence} calibrated={calibrated} />
            </div>
            {result.clarification && (
              <div className="mt-4 rounded-lg bg-saffron/10 p-3">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-saffron">
                  One question · {result.clarification.language} · about “{result.clarification.field}”
                </div>
                <p className="mt-1 text-sm">{result.clarification.question}</p>
              </div>
            )}
            {result.alternatives.length > 0 && (
              <div className="mt-4 space-y-2">
                <div className="text-[11px] font-semibold uppercase tracking-wide text-red-700">Two readings — not guessing</div>
                {result.alternatives.map((alt, i) => (
                  <div key={i} className="rounded-lg bg-red-50 p-2 text-xs">
                    <span className="font-medium">{alt.reason}</span>
                    {alt.geo && <span className="ml-2 font-mono text-gray-600">{alt.geo.lat.toFixed(4)}, {alt.geo.lng.toFixed(4)}</span>}
                  </div>
                ))}
              </div>
            )}
          </Card>
        )}
      </div>

      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <Card title="Structured">
            {result ? <StructuredPanel structured={result.structured} revealed={stage >= 1 ? DETERMINISTIC : new Set()} /> : <Placeholder />}
          </Card>
          <Card title="Landmarks">
            {result ? <LandmarkList landmarks={result.structured.landmarks} revealed={stage >= 2} /> : <Placeholder />}
          </Card>
        </div>
        <Card title="Location">
          <div className={`transition-opacity duration-500 ${stage >= 3 ? "opacity-100" : "opacity-30"}`}>
            <MapPin geo={stage >= 3 ? (result?.geo ?? null) : null} />
          </div>
          <div className={`mt-3 transition-opacity duration-500 ${stage >= 4 ? "opacity-100" : "opacity-0"}`}>
            <DigipinBadge digipin={result?.digipin ?? null} />
          </div>
        </Card>
        {result && (
          <Card title="Evidence" className={`transition-opacity duration-500 ${stage >= 4 ? "opacity-100" : "opacity-0"}`}>
            <EvidenceList evidence={result.evidence} />
          </Card>
        )}
      </div>
    </div>
  );
}

function Placeholder() {
  return <p className="text-sm text-gray-400">Resolve an address to see it here.</p>;
}
