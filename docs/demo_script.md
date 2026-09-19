# Demo script — 3 minutes

The video is the submission. Everything below is designed to be recorded in
one take with no editing, so that Sunday is re-recording, not first-recording.

**Before recording (10 minutes)**

- [ ] `sam deploy` with `ServingStack=E EnableOpenSearch=true`; `create_index --load --warm`; `bash scripts/smoke.sh` all `ok`
- [ ] Console running against the live API (`frontend/console/.env.local` has `VITE_API_URL`)
- [ ] Warm the Lambdas: resolve the two demo addresses once each, off camera
- [ ] Review queue is **empty** (approve or reject leftovers) so the DENY case is the only row
- [ ] Browser tabs, left to right: Console · CloudWatch dashboard · `docs/results/ablation.md` on GitHub · `ARCHITECTURE.md` diagram
- [ ] Browser zoom 125%, screen recorder at 1080p, mic checked
- [ ] Clock in the Guardrails screen preset to **23:00**

Say the words in the "Say" column; they are short on purpose. Do not read the screen aloud.

---

## 0:00–0:20 · The problem

| Show | Say |
|---|---|
| README, "The problem" section — the `h no 14 behind shiv mandir…` address | "This is a real Indian delivery address. It's not an address, it's directions for a human. So the rider phones the customer — on almost every order — and the first attempt still fails a lot." |
| | "And whatever the rider learns dies in their head. The next rider starts from zero." |

## 0:20–0:45 · Messy address in, DIGIPIN out

| Show | Say |
|---|---|
| Console → Playground. Paste: `h no 14 behind shiv mandir near gupta general store ramesh nagar delhi 110015 call before coming 9876543210` | "PataSetu takes that text…" |
| Click Resolve. Let the **stage reveal** run: fields appear, landmarks match, pin drops, DIGIPIN renders | "…normalises it, parses what's deterministic, retrieves landmarks it has seen before, and only then asks a model to structure it." |
| Point at the DIGIPIN badge | "The output is a DIGIPIN — India Post's official 10-character geocode — plus a calibrated confidence." |
| Point at the evidence line *stripped 1 phone number* | "The phone number was stripped before any model or log ever saw it." |

## 0:45–1:15 · The graph learns

| Show | Say |
|---|---|
| `docs/results/learning_curve.svg` (GitHub tab) | "The landmark graph learns from confirmed deliveries. We measured it: an empty graph, fed the corpus one address at a time." |
| Trace the green line: 6% → 81% | "With zero deliveries in a neighbourhood, six percent of addresses geocode from the graph. After **one** confirmed delivery — eighty-one. It's a step, not a slope: one delivery teaches the temple, and the next address behind the same temple is placed exactly." |
| Point at the orange line: 1,221 m → 0 m | "Median error goes from over a kilometre to zero." |

## 1:15–1:45 · One question, in the customer's language

| Show | Say |
|---|---|
| Playground. Paste: `शिव मंदिर के पीछे, रमेश नगर, दिल्ली 110015` | "When it genuinely doesn't know — here there's no house number — it doesn't guess." |
| Result: NEEDS_INFO, the clarification card with a **Hindi** question | "It asks one question, targeting the field that unlocks the most, in the script the customer wrote in. Devanagari in, Hindi out." |
| (If the deployed clarifier has run: the review-queue row shows the agent-generated question with `source: agent`) | "The question comes from a Strands agent on Nova Lite, with a template fallback that's labelled as such — we never let a template take credit for the agent." |

## 1:45–2:05 · Clock at 23:00 — Cedar says no

| Show | Say |
|---|---|
| Console → **Guardrails**. Clock slider already at 23:00, confidence 0.55 | "Before any message goes out, we ask Cedar. It's eleven at night for this customer." |
| Click **May we contact the customer?** → big red **DENY** | "Denied. No permit matched — the only permit is scoped to nine-to-eight." |
| Point at the reason text, then the policy file on the right | "The policy is a text file. An auditor reads this; so does the review queue." |
| Tick **Customer opted out**, click again → DENY with `@id("contact-opted-out")` chip | "Opt-out is a forbid with no principal named — even a human operator can't override it. A test caught the first draft that got that wrong." |
| Click the **review queue** link → the DEMO-23 row, reason in red | "A denial is never a silent drop. The case goes to a person, with the policy id." |

## 2:05–2:35 · What each stage is worth

| Show | Say |
|---|---|
| `docs/results/ablation.md` table | "We didn't just build it; we ablated it. Same gold set, cumulative configurations." |
| Point at A → R1 → R2 geo median column | "Regex and gazetteer alone: median error nine hundred and sixty-nine metres. Add landmark retrieval — zero. That's the graph." |
| Reliability diagram | "Confidence is isotonic-calibrated. Raw expected calibration error 0.22; cross-validated after fitting, 0.06. The number on screen means what it says." |
| `ARCHITECTURE.md` diagram | "Lambda on Graviton, DynamoDB, OpenSearch Serverless for hybrid retrieval, Bedrock — Nova Lite first, Claude only on escalation — Amazon Location, EventBridge to the learner, Cedar for policy, Cognito on the operator routes." |

## 2:35–3:00 · Dashboard and what we learned

| Show | Say |
|---|---|
| CloudWatch dashboard tab: resolution outcomes, latency p50/p95, cost | "Every request emits one metrics line — no extra calls — so this dashboard costs nothing to keep." |
| `docs/learnings.md`, scroll slowly | "Three things we learned. Government data has junk in it and validates silently. A fixed three-kilometre radius is a metro assumption. And the learning curve is a step: one delivery is enough." |
| README top | "PataSetu. The address stops being a phone call." |

---

## Fallbacks, in case something breaks on camera

| If | Then |
|---|---|
| Live API is slow or errors | Restart the console with `VITE_API_URL` unset — mock mode shows the same Ramesh Nagar example and the same Cedar rules, evaluated locally. Say nothing; keep going. |
| Clarifier question shows `source: template` | Say "template fallback — labelled, on purpose" and move on. It's a feature. |
| Guardrails returns ALLOW at 23:00 | The slider moved. Check the hour label reads 23:00 before clicking. |
| Review queue is not empty | Do the DENY beat anyway; the DEMO-23 row will be at the bottom (oldest first). Scroll. |

## After the take

- [ ] Watch it back in full. Write every flaw in `docs/learnings.md` under "video notes". Do not fix any tonight.
- [ ] `sam deploy --parameter-overrides EnableOpenSearch=false`
