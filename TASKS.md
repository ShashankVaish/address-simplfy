# Tasks — PataSetu

**Three build days. One buffer day.** 17–20 September 2026.

| Day | Role |
|---|---|
| **Wed 16** | Day 0 — access, credits, data collection. No product code |
| **Thu 17** | Build day 1 — the spine, **deployed live by tonight** |
| **Fri 18** | Build day 2 — intelligence, **second deploy** |
| **Sat 19** | Build day 3 — depth, **final deploy**, feature freeze, first video take |
| **Sun 20** | Buffer — bug fixes, re-record, blog, submit |

Owners: **A** pipeline · **B** data + infra · **C** agents + policy · **D** frontend.
Legend: `[ ]` todo · **P0** must ship · **P1** expected · **P2** first to cut.

> ### The one rule that makes this work
> **Deploy on Thursday, not Friday.** A team that deploys on day three discovers its IAM problems on day three. We deploy a near-empty stack on day one and grow it. Every subsequent deploy is a small delta, not a first attempt.

---

## Contents

- [Deployment strategy](#deployment-strategy)
- [Day 0 — Wednesday](#day-0--wednesday-16-sept)
- [Day 1 — Thursday](#day-1--thursday-17-sept--the-spine-live)
- [Day 2 — Friday](#day-2--friday-18-sept--intelligence)
- [Day 3 — Saturday](#day-3--saturday-19-sept--depth--freeze--first-take)
- [Day 4 — Sunday](#day-4--sunday-20-sept--buffer-and-submit)
- [Kill list](#kill-list)
- [Standing rules](#standing-rules)

---

## Deployment strategy

Three planned deploys. Each one is smoke-tested before anyone goes to sleep.

| Deploy | When | What goes live | Smoke test passes when |
|---|---|---|---|
| **D1** | Thu ~18:00 | DynamoDB, S3, HTTP API, `resolver` Lambda running S0+S1 only | `curl` returns structured JSON with a pincode parsed |
| **D2** | Fri ~18:00 | OpenSearch, Bedrock, Location Service, full S0→S7 | `curl` returns a DIGIPIN and a confidence score |
| **D3** | Sat ~19:00 | Step Functions, Cedar authorizer, learner, EventBridge, final console | Cedar DENY path works end to end |

### Smoke test — run after every deploy, no exceptions

```bash
# backend/scripts/smoke.sh
set -e
API=$(aws cloudformation describe-stacks --stack-name patasetu \
      --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)

echo "-> health"
curl -sf "$API/health" | jq -e '.status=="ok"'

echo "-> resolve (clean address)"
curl -sf -X POST "$API/v1/resolve" -H 'content-type: application/json' \
  -d '{"raw":"h no 14 ramesh nagar delhi 110015"}' \
  | jq -e '.structured.pincode=="110015"'

echo "-> resolve (landmark address)"
curl -sf -X POST "$API/v1/resolve" -H 'content-type: application/json' \
  -d '{"raw":"behind shiv mandir near gupta store ramesh nagar delhi 110015"}' \
  | jq -e '.digipin != null and .confidence > 0'

echo "OK smoke passed"
```

Wire this into `.github/workflows/deploy.yml` so a bad deploy is caught by CI, not by a judge.

### Rollback

`main` is always deployable. If a deploy breaks the demo path:

```bash
git revert <sha> && sam build && sam deploy   # ~4 minutes
```

Never debug a broken deploy at midnight. Revert first, diagnose in the morning.

### Cost discipline

```bash
# Run every night before sleeping
./backend/scripts/teardown.sh     # kills the OpenSearch Serverless collection
```

OpenSearch Serverless bills on provisioned capacity by the hour, idle or not. Bring it up when work starts, tear it down when work stops.

---

## Day 0 — Wednesday 16 Sept

> Rules allow learning and practice beforehand. **Project work starts when the clock does.** Everything here is access, setup, and data collection.

### Blocking access — in this order, today

- [ ] **P0** · *all* · WeMakeDevs account, checked in to First Commit
- [ ] **P0** · *all* · AWS Builder Center profile created **and student status verified** — registration is incomplete without it
- [ ] **P0** · *B* · **Request Bedrock model access in `ap-south-1`**: Amazon Nova Lite, Claude, Titan Text Embeddings v2
  > Hours, not minutes. This is the single most common day-one blocker. If a model is unavailable in `ap-south-1`, decide now between a cross-region inference profile and `us-east-1` with added latency — do not discover this on Friday.
- [ ] **P0** · *B* · Redeem the $100 participant credits
- [ ] **P0** · *B* · AWS Budgets alert at **$25**, email action
- [ ] **P0** · *B* · Confirm IAM: everyone can `sam deploy`. A permissions surprise on Thursday costs half a day
- [ ] **P1** · *B* · Verify the **Cedar Python path** works. If it fights back, plan the separate `authorizer` Lambda now

### Data — the highest-value hour of the week

- [ ] **P0** · *C* · Collect **120 real addresses** from WhatsApp groups, hostel-mates, family. With consent
- [ ] **P0** · *C* · Anonymise — strip names, remove phone numbers, perturb identifying house numbers
- [ ] **P1** · *B* · Public India Post pincode data into `backend/data/pincodes.csv`
- [ ] **P1** · *B* · Locate the **official DIGIPIN open-source implementation**. Read it. Write nothing yet

### Team

- [ ] **P0** · *all* · Repo created, `main` protected, everyone can push
- [ ] **P0** · *all* · Read [ARCHITECTURE.md](./ARCHITECTURE.md) end to end. Agree the A/B/C/D split
- [ ] **P0** · *all* · Toolchain installed: Python 3.12, Node 20, SAM CLI, Docker, AWS CLI on `ap-south-1`
- [ ] **P1** · *D* · Paper or Figma pass on the **Playground** screen — the Best UI entry and the video's opening shot
- [ ] **P1** · *all* · Discord joined; Bangalore build-day seats claimed if attending

> ### Day 0 gate
> Bedrock access **requested**, budget alert live, 120 addresses collected, everyone knows what they own and can deploy.

---

## Day 1 — Thursday 17 Sept · **The spine, live**

> Goal by midnight: a **public URL** that parses an address, plus a labelled gold set and a measured baseline. The result will be bad. Record it anyway — it is the number you beat on camera.

### 09:00–10:00 — Freeze the contract (whole team, together)

- [ ] **P0** · *all* · **Freeze `shared/contract.ts`** — the exact `/v1/resolve` response shape. One hour, everyone in the room, before any code
- [ ] **P0** · *all* · Confirm the day's gate and who is blocked on whom

This single hour is what lets four people work in parallel instead of blocking each other.

### 10:00–14:00 — Four parallel tracks

**B — infrastructure first, so the deploy isn't a cliff**
- [ ] **P0** · `template.yaml` — DynamoDB + GSI-1, S3 bucket, HTTP API, `resolver` Lambda stub, `/health`
- [ ] **P0** · **`sam deploy` before lunch.** A stack must exist by 14:00, even if the Lambda returns `{"status":"ok"}`
- [ ] **P0** · `layers/common/providers.py` — the cloud/local switch. 45 minutes now saves a day later
- [ ] **P0** · `layers/common/models.py` — shared Pydantic types

**A — the deterministic stages**
- [ ] **P0** · `normalize.py` — NFKC fold, whitespace collapse, transliteration, ~120-entry abbreviation table
- [ ] **P0** · `parse.py` — pincode regex + validation, house/flat/floor/block patterns, fuzzy city and state
- [ ] **P0** · **Phone stripping** into a separate field — never reaches a model or the embedding API

**C — corpus and gold set, before any pipeline code**
- [ ] **P0** · `eval/corpus_gen.py` — perturb 120 seeds into **2,000 addresses**: drop pincode, misspell landmark, transliterate, abbreviate, reorder, merge lines, inject phone number
- [ ] **P0** · `eval/label_tool.py` — a 40-line CLI for labelling
- [ ] **P0** · `eval/metrics.py` — field F1, exact match, haversine, precision@threshold

**D — console shell against a mock**
- [ ] **P0** · Vite + React + Tailwind scaffold, routing, layout
- [ ] **P0** · `api/mock.ts` returning contract-shaped fixtures
- [ ] **P0** · **Playground screen** — textarea, structured panel, confidence bar, evidence list

### 14:00–18:00 — DIGIPIN, gazetteer, labelling

- [ ] **P0** · *B* · Vendor the official encoder into `digipin.py`. **Do not hand-roll it**
- [ ] **P0** · *B* · `tests/test_digipin.py` — assert known (lat, lng) to code pairs from the India Post portal
- [ ] **P0** · *B* · `scripts/load_pincodes.py` — gazetteer into DynamoDB
- [ ] **P0** · *C* · Hand-label the **300-address gold set**. Split 150 dev / 150 test. All four people label 75 each — it goes fast and calibrates everyone's idea of "correct"
- [ ] **P0** · *A* · `scripts/resolve_one.py` — CLI running S0 + S1, no cloud
- [ ] **P1** · *D* · `SpanHighlighter` — colour raw text by resolved field

### 18:00–20:00 — **Deploy D1**

- [ ] **P0** · *B* · `resolver` Lambda runs S0 + S1 for real. `sam build && sam deploy`
- [ ] **P0** · *B* · `scripts/smoke.sh` written and passing
- [ ] **P0** · *D* · Console pointed at the live URL for the fields that exist
- [ ] **P0** · *B* · `scripts/teardown.sh` written (nothing to tear down yet, but write it now)

### 20:00–22:00 — Measure the baseline

- [ ] **P0** · *C* · Run configuration **A** (deterministic only) on the **dev** split
- [ ] **P0** · *C* · Write the number into `docs/results/ablation.md`
- [ ] **P1** · *all* · Ten minutes: what surprised us today, into `docs/learnings.md`

> ### Day 1 gate (22:00)
> - [ ] **A public URL exists and parses an address.** `smoke.sh` passes
> - [ ] `pytest tests/test_digipin.py` passes against real India Post values
> - [ ] Gold set labelled: 150 dev + 150 test
> - [ ] **Configuration A measured and written down**
> - [ ] Playground renders against the live API
>
> If the URL does not exist tonight, stop feature work tomorrow morning until it does.

---

## Day 2 — Friday 18 Sept · **Intelligence**

> Goal by tonight: the **full S0 to S7 pipeline live**, returning DIGIPIN and confidence, with ablation rows A–D measured.

### 09:00–13:00 — Retrieval and the landmark graph

**B**
- [ ] **P0** · `scripts/create_index.py` — OpenSearch Serverless collection; `landmarks` index with `knn_vector` (1024-d, HNSW, cosine) and `geo_point`
- [ ] **P0** · `search.py` — client, upsert, index management
- [ ] **P0** · `scripts/warm_landmarks.py` — extract landmarks from the corpus, embed in **batch**, index
- [ ] **P0** · `geocode.py` — landmark graph, then Amazon Location Service, then pincode centroid, each tagging `geo.source`

**A**
- [ ] **P0** · `retrieve.py` — **one** OpenSearch query carrying BM25 + kNN + `geo_distance <= 3 km`
- [ ] **P0** · **RRF fusion**, `k = 60`, plus `tests/test_rrf.py`

**C**
- [ ] **P0** · `confidence.py` — the seven features, hand-set weights for now
- [ ] **P0** · `decide.py` — `RESOLVED` / `NEEDS_INFO` / `AMBIGUOUS`

**D**
- [ ] **P0** · Playground on live data: map pin (MapLibre), `DigipinBadge`, evidence list
- [ ] **P1** · Review queue against `GET /v1/queue` (GSI-1)

### 13:00–17:00 — Bedrock, cascade, cache

- [ ] **P0** · *A* · `prompts/structure.txt` — the five rules: schema-only output · never guess · pick landmarks from the candidate list · per-field confidence · return `alternatives`
- [ ] **P0** · *A* · `structure.py` — Bedrock call, strict JSON parse, one retry on invalid JSON
- [ ] **P0** · *A* · **Model cascade** — skip the model entirely when S1 is complete with a valid pincode and no landmark dependence, then Nova Lite, then escalate to Claude on low field confidence, conflict, multi-script, or non-empty `alternatives`
- [ ] **P0** · *A* · Wire S0 to S7 in `functions/resolver/app.py`; emit `evidence[]`
- [ ] **P0** · *A* · Exact cache — `sha256(normalised)` to DynamoDB, 90-day TTL
- [ ] **P1** · *B* · Emit `EscalationRate` and `CacheHitRate` as CloudWatch metrics
- [ ] **P2** · *A* · Near-duplicate cache — embedding kNN, cosine > 0.97, same pincode

### 17:00–19:00 — **Deploy D2**

- [ ] **P0** · *B* · `template.yaml` grows: OpenSearch access policy, Bedrock IAM, Location Service, EventBridge bus
- [ ] **P0** · *B* · `sam deploy`; `smoke.sh` now asserts a DIGIPIN and a non-zero confidence
- [ ] **P0** · *D* · Delete the mock path — console is fully on live data
- [ ] **P1** · *D* · Cognito on the console; leave the playground public and read-only
- [ ] **P0** · *B* · Provisioned concurrency = 1 on `resolver`, scheduled for tomorrow's recording window

### 19:00–22:00 — Measure

- [ ] **P0** · *C* · `eval/run_ablation.py` — configurations **A, B, C, D** on the **dev** split
- [ ] **P0** · *C* · Fill those four rows in `docs/results/ablation.md`
- [ ] **P1** · *D* · Metrics screen scaffold — `AblationTable`, `LearningCurve` placeholders
- [ ] **P1** · *all* · Ten minutes into `docs/learnings.md`
- [ ] **P0** · *B* · **Run `teardown.sh`**

> ### Day 2 gate (22:00)
> - [ ] The public URL resolves a messy landmark address end to end, returning DIGIPIN and confidence
> - [ ] Ablation rows A–D measured on dev
> - [ ] Console runs entirely on the live API
> - [ ] `teardown.sh` run
>
> **If this gate slips, cut kill-list items 1 and 2 tomorrow morning.** Do not extend the day.

---

## Day 3 — Saturday 19 Sept · **Depth · freeze · first take**

> Optional in-person day in Bangalore, 8 AM to 8 PM. The feedback session is worth more than four extra coding hours — **take a working demo and let them break it.**
>
> Goal by tonight: everything shipped, feature-frozen, and **one complete video take recorded**. Sunday should be re-recording, not first-recording.

### 09:00–13:00 — Learning loop, Cedar, calibration

**C — the heaviest day of the four**
- [x] **P0** · `functions/learner/app.py` — on `DeliveryConfirmed`: `observation_count += 1`, EMA nudge on coordinates, alias upsert
- [x] **P0** · EventBridge rules wiring resolver to learner
- [x] **P0** · `policies/contact.cedar` — quiet hours 09:00–20:00, one message per order, opt-out `forbid`
- [x] **P0** · `policies/overwrite.cedar` — confidence > 0.95 **and** `geo_source == landmark_graph`
- [x] **P0** · `functions/authorizer/app.py` — Cedar evaluation, **fail closed** on error
- [x] **P0** · Denial routes to the review queue with policy id and reason. **Never a silent drop**
- [x] **P0** · `tests/test_cedar_policies.py` — quiet hours, opt-out, second message

**A — clarification agent**
- [x] **P0** · `functions/clarifier/agent.py` — Strands agent, one question targeting the highest-information missing field
- [x] **P0** · Language mirroring — Devanagari in, Hindi out

**B — observability**
- [x] **P0** · One CloudWatch dashboard: `ResolutionRate`, `ClarificationRate`, `EscalationRate`, `CostPerAddress`, `CacheHitRate`, p95 latency
- [x] **P1** · Structured JSON logs with `correlation_id` across every stage

**D — the Best UI entry**
- [ ] **P0** · Playground **stage-by-stage reveal** — deterministic fields appear, landmarks match, pin drops, DIGIPIN renders
- [ ] **P0** · Review queue: one-click approve / edit, with the reason confidence is low

### 13:00–16:00 — Calibration and the learning curve

- [x] **P0** · *C* · Fit **isotonic regression** on the dev split
- [x] **P0** · *C* · `eval/reliability.py` produces `docs/results/reliability.png`
- [ ] **P0** · *C* · Choose the threshold that hits **precision >= 95%**; record the resulting clarification rate
- [x] **P0** · *C* · `eval/learning_curve.py` produces `docs/results/learning_curve.png`
- [ ] **P1** · *A* · `statemachines/clarification.asl.json` — authorise, send, wait-for-callback (6 h timeout), re-resolve, escalate
- [ ] **P1** · *D* · Map view: pins coloured by confidence, landmark layer
- [ ] **P1** · *D* · Metrics screen with real numbers
- [ ] **P1** · *D* · Rider PWA: address card, hold-to-record mic to presigned S3, Delivered / Couldn't find
- [ ] **P2** · *C* · Voice notes: S3, Transcribe (hi-IN/en-IN), Strands extraction, `access_notes`
- [ ] **P2** · *C* · SNS SMS delivery
- [ ] **P2** · *D* · Landmark explorer

### 16:00–19:00 — **Deploy D3, then freeze**

- [ ] **P0** · *B* · Final `sam deploy`. `smoke.sh` extended to assert the **Cedar DENY path**
- [ ] **P0** · *all* · **Feature freeze at 19:00.** After this, only bug fixes touch the code
- [ ] **P0** · *all* · Everyone pulls `main`, runs the demo path themselves, and reports what breaks
- [ ] **P0** · *B* · Deploy from a **clean clone** to prove the README setup steps actually work

### 19:00–23:00 — First video take

This is the change that makes Sunday a real buffer.

- [x] **P0** · *D* · `docs/demo_script.md` finalised, storyboarded
- [ ] **P0** · *all* · Warm the Lambda path before recording
- [ ] **P0** · *all* · **Record one complete take**, following the beat sheet:

| Time | On screen |
|---|---|
| 0:00–0:20 | The problem — a rider calling one customer three times |
| 0:20–0:45 | Paste a messy Hindi-English address, get structured output, pin, and **DIGIPIN** |
| 0:45–1:15 | Learning curve rising as a locality feeds in |
| 1:15–1:45 | Low-confidence address, one targeted Hindi question, reply, RESOLVED |
| 1:45–2:05 | **Clock at 23:00, Cedar DENY, human review queue** |
| 2:05–2:35 | Ablation table, then architecture with services named |
| 2:35–3:00 | CloudWatch dashboard; **what we learned** |

- [ ] **P0** · *all* · Watch it back in full. Write down every flaw — do not fix them tonight
- [x] **P0** · *A* · Blog post **first draft** written (not published)
- [ ] **P0** · *B* · **Run `teardown.sh`**

> ### Day 3 gate (23:00)
> - [ ] D3 deployed; `smoke.sh` passes including the Cedar DENY assertion
> - [ ] **One complete video take exists**, however rough
> - [ ] Blog draft written
> - [ ] Reliability diagram and learning curve exported
> - [ ] Code frozen

---

## Day 4 — Sunday 20 Sept · **Buffer and submit**

> Not a free day. It carries the test-split run, the final video, the blog, and the submission. **Treat 15:00 as the deadline**, not midnight.

### 09:00–09:30 — Triage

List every flaw from last night's take and every bug found since. Sort into three buckets, and work them strictly in order:

| Bucket | Rule |
|---|---|
| **Breaks the demo path** | Fix now. Nothing else matters |
| **Visible but survivable** | Fix only if bucket 1 is empty by 11:00 |
| **Everything else** | Write it into the README limitations section. Do not fix |

A bug a judge will never see is not a bug today. It is a line in `## Limitations`.

### 09:30–12:00 — Fix and measure

- [ ] **P0** · *all* · Bucket-1 fixes. Re-run `smoke.sh` after **every** change
- [ ] **P0** · *C* · Run the **test** split. **Exactly once.** No peeking, no re-tuning afterwards
- [ ] **P0** · *C* · Complete configuration **E** — all five ablation rows filled with real numbers
- [ ] **P0** · *B* · Provisioned concurrency on for the recording window

### 12:00–14:00 — Final video

- [ ] **P0** · *all* · Warm the path. Re-record, fixing last night's flaws
- [ ] **P0** · *D* · State on screen, once, that the data is synthetic
- [ ] **P0** · *all* · Watch it back in full. **If it drags anywhere, cut — do not explain**
- [ ] **P0** · *D* · Export and upload

### 14:00–15:00 — Write and submit

- [ ] **P0** · *A* · Blog post **published on AWS Builder Center**, linked in the submission
- [ ] **P0** · *all* · `docs/learnings.md` finalised — a first agent, a first deploy, a first calibration. **Judging rewards this explicitly**
- [ ] **P0** · *B* · README: architecture diagram, setup steps, honest limitations
- [ ] **P0** · *all* · Walk the [acceptance checklist](./REQUIREMENTS.md#5-acceptance-criteria--the-sunday-checklist) line by line
- [ ] **P0** · *all* · **Submit**

### 15:00 onwards — Reserve

Everything after 15:00 is genuine slack. Use it for bucket-2 fixes, a better thumbnail, or nothing at all. Do not use it to start a feature.

> ### Final gate
> Submitted, with the video uploaded, the blog live and linked, and the repo public.

---

## Kill list

Decided Thursday. Applied the moment a gate slips. Do not renegotiate at 2 am.

| Order | Cut | Replacement |
|---|---|---|
| 1 | Rider voice notes + Transcribe | Typed note field in the console |
| 2 | Step Functions wait-for-reply + SMS | Return the question in the API response; collect the answer in the console |
| 3 | Bulk CSV ingest | Paste addresses one at a time in the playground |
| 4 | Landmark explorer screen | Landmarks visible on the map layer only |
| 5 | Rider PWA entirely | Show the address card as a screen inside the console |

**Never cut:** the evaluation harness · the Cedar denial demo · the playground · the review queue · the Saturday video take.

---

## Standing rules

1. **Deploy on day one.** Every later deploy is then a small delta, not a first attempt.
2. **`smoke.sh` after every deploy.** No exceptions, including the 2 am one.
3. **Revert, don't debug, at night.** `main` is always deployable.
4. **Measure before you optimise.** *"We went from 0.58 to 0.91"* only exists if Thursday recorded a bad number.
5. **The contract is frozen.** Changing `/v1/resolve`'s shape after Thursday blocks three people to unblock one.
6. **Run `teardown.sh` every night.** OpenSearch Serverless bills while idle.
7. **Saturday 19:00 is the feature freeze.** A feature added Sunday is a bug shipped Sunday.
8. **Never say "we built this for Amazon."** Say it is for every logistics operator in India. Stronger claim, and a truer one.
