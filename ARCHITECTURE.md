# Architecture — PataSetu

> Companion to [REQUIREMENTS.md](./REQUIREMENTS.md) and [TASKS.md](./TASKS.md).
> Region: `ap-south-1` (Mumbai) — data residency and lowest latency for Indian users.

---

## Contents

1. [Design principles](#1-design-principles)
2. [System context](#2-system-context)
3. [Request flow](#3-request-flow)
4. [The resolution pipeline, stage by stage](#4-the-resolution-pipeline-stage-by-stage)
5. [The landmark knowledge graph](#5-the-landmark-knowledge-graph)
6. [Confidence scoring and calibration](#6-confidence-scoring-and-calibration)
7. [The clarification agent and Cedar](#7-the-clarification-agent-and-cedar)
8. [Data model](#8-data-model)
9. [AWS service map](#9-aws-service-map)
10. [Efficiency — latency, cascade, caching](#10-efficiency--latency-cascade-caching)
11. [Frontend architecture](#11-frontend-architecture)
12. [Evaluation harness](#12-evaluation-harness)
13. [Local and Build It mode](#13-local-and-build-it-mode)
14. [Observability](#14-observability)
15. [Failure modes and fallbacks](#15-failure-modes-and-fallbacks)

---

## 1. Design principles

Five decisions shape everything below. Each one is defensible out loud in the demo.

1. **The LLM is stage three, not stage one.** Cheap deterministic work runs first and frequently finishes the job. Calling Bedrock on every raw string is slower and roughly ten times more expensive per address.
2. **Never guess.** A confidently wrong flat number is worse than an admitted gap, because it fails silently at the doorstep. `null` is a valid answer; a fabricated value is not.
3. **Learning never sits in the request path.** Graph writes, voice-note extraction, and calibration refits are all asynchronous.
4. **Uncertainty is a first-class output.** Confidence is fitted against held-out data and reported with a reliability diagram, not asserted.
5. **Automation needs an executable boundary.** Cedar policies, not good intentions, decide whether a customer gets messaged.

---

## 2. System context

```
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ Ops console │   │  Rider PWA  │   │ Bulk CSV /  │
│  (Amplify)  │   │  (Amplify)  │   │ partner API │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │                 │                 │
       └────────┬────────┴─────────────────┘
                ▼
        ┌───────────────┐
        │ API Gateway   │  HTTP API · WAF · throttle · Cognito authorizer
        └───────┬───────┘
                ▼
        ┌───────────────────────────────────────────┐
        │            Lambda: resolver               │
        │   S0 → S1 → S2 → S3 → S4 → S5 → S6 → S7   │
        └───┬───────────┬──────────┬────────────┬───┘
            │           │          │            │
            ▼           ▼          ▼            ▼
      ┌──────────┐ ┌─────────┐ ┌────────┐ ┌──────────┐
      │ DynamoDB │ │OpenSearch│ │Bedrock │ │ Location │
      │  cache,  │ │ landmark │ │ Nova / │ │ Service  │
      │  state,  │ │  graph   │ │ Claude │ │ geocode  │
      │  audit   │ │          │ │ Titan  │ │          │
      └──────────┘ └─────────┘ └────────┘ └──────────┘
                │
                ▼  EventBridge (AddressResolved · AddressNeedsInfo · DeliveryConfirmed)
     ┌──────────┴──────────┬──────────────────┐
     ▼                     ▼                  ▼
┌──────────┐      ┌────────────────┐   ┌────────────┐
│ Lambda:  │      │ Step Functions │   │ Lambda:    │
│ learner  │      │ clarification  │   │ ingest     │
│ (graph   │      │  ┌──────────┐  │   │ (S3 bulk)  │
│  write)  │      │  │  Cedar   │  │   └────────────┘
└──────────┘      │  │authorize │  │
                  │  └────┬─────┘  │
                  │       ▼        │
                  │  SNS / review  │
                  │     queue      │
                  └────────────────┘
```

---

## 3. Request flow

### 3.1 Synchronous — `POST /v1/resolve`

```
Client
  │  raw address (+ optional GPS hint)
  ▼
API Gateway ──▶ Lambda: resolver
                  │
                  ├─ S0 normalise + cache probe ──▶ DynamoDB
                  │     └─ HIT ──────────────────────────────┐
                  ├─ S1 deterministic parse                  │
                  ├─ S2 hybrid retrieval ──▶ OpenSearch      │
                  ├─ S3 structuring ──▶ Bedrock              │
                  ├─ S4 geocode ──▶ graph | Location | centroid
                  ├─ S5 DIGIPIN (local, pure function)       │
                  ├─ S6 confidence (calibrated)              │
                  └─ S7 decide ──────────────────────────────┤
                                                             ▼
                                   RESOLVED / NEEDS_INFO / AMBIGUOUS
                                                             │
                        emit EventBridge event ◀─────────────┘
                        (fire-and-forget, never blocks response)
```

The API **never blocks waiting for a human.** A `NEEDS_INFO` response returns immediately, carrying the question text. The outreach workflow runs on its own clock.

### 3.2 Asynchronous — clarification

```
EventBridge: AddressNeedsInfo
        ▼
Step Functions state machine
  1. GenerateQuestion        (Lambda → Strands agent → Bedrock)
  2. AuthorizeContact        (Lambda → Cedar)
        ├─ DENY  ──▶ write to review queue with policy id + reason ──▶ END
        └─ ALLOW ──▶ continue
  3. SendMessage             (SNS)
  4. WaitForCallback         (task token, 6-hour timeout)
        ├─ reply  ──▶ 5. ReResolve ──▶ 6. EmitResolved ──▶ END
        └─ timeout ──▶ write to review queue ──▶ END
```

The wait-for-callback-with-timeout pattern is native to Step Functions. Hand-rolling it in Lambda is where weekends die.

### 3.3 Asynchronous — learning

```
Rider taps "Delivered"                Rider holds mic button
        │                                      │
        ▼                                      ▼
EventBridge: DeliveryConfirmed          S3 putObject (audio)
        │                                      │
        ▼                                      ▼
Lambda: learner                         S3 event ──▶ Lambda: learner
  • observation_count += 1                • Transcribe (hi-IN / en-IN)
  • EMA nudge on coordinates              • Strands agent extracts facts
  • upsert alias if new spelling          • upsert access_notes on
  • reindex embedding if name changed       LMK + DIGIPIN cell
        │                                      │
        └──────────────┬───────────────────────┘
                       ▼
             OpenSearch: landmarks
```

---

## 4. The resolution pipeline, stage by stage

### S0 — Normalisation and cache probe

| Step | Detail |
|---|---|
| Unicode fold | NFKC, collapse whitespace and punctuation, lowercase |
| Script handling | Keep the original **and** a transliterated form. Both enter retrieval — `मंदिर` and `mandir` must both hit |
| Abbreviation expansion | ~120-entry table: `h.no/hno/house no`, `opp/opposite`, `nr/near`, `apts/apartments`, `blr→Bengaluru`, `gzb→Ghaziabad` |
| Cache probe | `sha256(normalised)` → DynamoDB point read |

The cache is not a micro-optimisation. In any real city corpus a large share of addresses are near-duplicates (same apartment complexes, same hostels), so this is the single biggest cost lever in the system.

**Cost: 0. Latency: ~8 ms (one DynamoDB read). A hit ends the request at ~12 ms total.**

### S1 — Deterministic parse

Regex and lookup tables extract what is unambiguous. No model involved.

- **6-digit pincode** → validated against a gazetteer table (pincode → locality, district, state, centroid lat/lng)
- **House / flat / plot number** patterns; floor and block tokens; tower names
- **State and city** from a gazetteer with fuzzy matching for misspellings
- **Phone numbers** → stripped into a separate field, never into the address (PII hygiene, NFR-25)

A valid pincode is enormously informative: it pins locality, district, state and a centroid, and it lets S2 constrain retrieval geographically instead of searching all of India.

**Cost: 0. Latency: < 1 ms. Some clean addresses exit the pipeline here with zero model cost.**

### S2 — Hybrid candidate retrieval

Three independent signals, one OpenSearch round trip, fused with Reciprocal Rank Fusion.

| Signal | Query | Catches |
|---|---|---|
| BM25 lexical | `match` on `canonical_name` + `aliases`, boosted on exact tokens | Correct spellings, distinctive proper nouns |
| Dense vector | `knn` over Titan Text Embeddings v2 (1024-d, cosine) | "Sunrise Apts" ≈ "Sunrise Apartment"; transliteration variants; word-order changes |
| Geo filter | `geo_distance ≤ 3 km` from pincode centroid or GPS hint | Kills the 400 other "Shiv Mandir"s in India — the highest-value filter in the system |

**RRF over a tuned linear blend**, deliberately: it needs no score normalisation across incompatible scales, and it degrades gracefully when one signal is missing (no pincode, no hint).

```
score(d) = Σ_i  1 / (k + rank_i(d)),  k = 60
```

**Latency: ~45 ms.**

### S3 — LLM structuring with constrained output

Only now does a model see the text — and it sees it *with* the deterministic fields and the retrieved candidates already in the prompt. Its job is narrow: assign remaining spans to fields, attach landmark relations, pick from the supplied candidate list.

```
SYSTEM PROMPT — the five rules that matter

1. Output ONLY JSON matching the given schema. No prose.
2. Use null for any field not present. NEVER guess a value.
3. Landmark matches must be chosen from the candidate list by id.
   If none fits, set matched_id to null and keep the raw name.
4. Return per-field confidence in [0,1] and a one-line reason per field.
5. If two readings are equally plausible, return both in `alternatives`
   rather than silently picking one.
```

Rules 2 and 5 are the difference between a demo and a liability.

**Latency: ~600 ms (Nova Lite). Escalation to Claude adds ~900 ms, on a minority of calls.**

### S4 — Geocoding, cheapest source first

| Order | Source | Why |
|---|---|---|
| 1 | **Landmark graph hit** | Free, instant, and the most accurate source we have — it was learned from actual deliveries |
| 2 | **Amazon Location Service** | Geocode the structured address when no landmark matched |
| 3 | **Pincode centroid** | Last resort. Marked `source: pincode_centroid`, confidence penalised. **Never presented as a doorstep** |

**Latency: 0–120 ms. Zero on a graph hit — the common case once the graph is warm.**

### S5 — DIGIPIN generation

A local pure function `digipin(lat, lng) → "XXX-XXX-XXXX"`, using the official open-source implementation published by the Department of Posts. No network call, no cost, deterministic, offline-capable.

> **Do not hand-roll the encoder.** The final DIGIPIN specification changed some characters in the alphabet relative to earlier beta versions. Vendor the official implementation and unit-test known (lat, lng) → code pairs against the India Post portal. Twenty minutes on Day 1 prevents a silent, invisible, demo-destroying bug.

**Latency: < 1 ms. Cost: 0.**

### S6 / S7 — Score and decide

See [§6](#6-confidence-scoring-and-calibration). Output is one of three states:

| Status | Meaning | Downstream |
|---|---|---|
| `RESOLVED` | Above threshold | Emit `AddressResolved`, return |
| `NEEDS_INFO` | Below threshold, one field clearly missing | Emit `AddressNeedsInfo` with the question |
| `AMBIGUOUS` | Two viable candidates far apart | Surface both to a human, never guess |

---

## 5. The landmark knowledge graph

This is what makes PataSetu a system rather than a prompt.

```jsonc
// OpenSearch document — index: landmarks
{
  "landmark_id": "LMK#DL#4412",
  "canonical_name": "Shiv Mandir",
  "aliases": ["शिव मंदिर", "shiv mandir", "shiv temple", "mandir wali gali"],
  "type": "religious",
  "location": { "lat": 28.65198, "lon": 77.12013 },   // geo_point
  "digipin_cell": "39J-49L-L8T2",
  "pincode": "110015",
  "embedding": [ /* 1024 floats */ ],                  // knn_vector
  "observation_count": 47,
  "confidence": 0.94,
  "access_notes": [
    { "text": "gate 3, guard se poocho, left wali lift",
      "source": "rider_voice", "upvotes": 6 }
  ],
  "last_seen": "2026-09-19T11:04:00Z"
}
```

### Three write paths

1. **Successful delivery.** `observation_count += 1`; coordinates nudged toward the confirmed delivery point by exponential moving average — resistant to one bad GPS reading.
2. **Rider voice note.** Transcribe converts the audio; a Strands agent extracts structured access facts; they attach to the landmark and to the DIGIPIN cell.
3. **Customer clarification.** An answered question becomes ground truth and back-propagates into the graph.

### Why this compounds

Because the graph is keyed on **DIGIPIN cells and landmark entities** rather than on address strings, knowledge transfers across differently-worded addresses for the same place. What we learn from customer A's badly-written address improves resolution for customer B in the same building who wrote theirs differently.

> **The chart that wins the demo:** feed the corpus locality by locality and plot resolution rate against cumulative addresses processed for that locality. The curve rises. Caption it *"the system gets better at your neighbourhood the more it sees of it."* Judges remember curves; they forget feature lists.

---

## 6. Confidence scoring and calibration

A confidence number nobody has validated is decoration.

### Features

| Feature | Range | Intuition |
|---|---|---|
| Field completeness | 0–1 | Weighted — flat number matters far more than state |
| Pincode–locality agreement | 0/1 | Pincode says Ramesh Nagar, text says BTM → hard conflict |
| Best landmark match score | 0–1 | Fused RRF score of the top candidate |
| Landmark observation count | log-scaled | Seen 47 times → trustworthy. Seen once → not |
| Geo source tier | 0.3 / 0.7 / 1.0 | centroid / geocoder / landmark graph |
| Candidate spread | 0–1 | Two candidates 4 km apart with near-equal scores → ambiguous |
| Model self-reported confidence | 0–1 | A **feature**, never the answer — LLMs are poorly calibrated |

### Calibration

Blend features with hand-set weights → raw score. Then fit **isotonic regression** from raw score to observed correctness on the held-out dev split.

Isotonic is the right choice here: monotonic, non-parametric, and stable on a few hundred labelled examples where a logistic fit would be shakier.

### Reliability diagram

Predicted confidence on x, observed accuracy on y, diagonal drawn in. If the 0.9 bucket is right 89% of the time, the number means something. This plot goes in the blog post — it is the clearest possible signal of engineering seriousness.

### Threshold

Do not pick 0.8 because it sounds round. Choose the threshold that hits a stated **precision target** — "at least 95% of auto-resolved addresses are correct" — and report the resulting clarification rate. Stating the trade-off explicitly is what reads as senior.

---

## 7. The clarification agent and Cedar

### Question selection

The agent computes which single missing field carries the most confidence gain, and asks about that one. Not a form. One question.

| Missing / uncertain | Question sent |
|---|---|
| Flat number absent, building matched | "Sunrise Apartments mil gaya. Flat number kya hai — aur kaunsa floor?" |
| Two candidate localities | "Aapka ghar Ramesh Nagar me hai ya Rajouri Garden me? (110015 / 110027)" |
| Landmark unknown to graph | "'Gupta General Store' hume nahi mila. Koi aur nishani — school, bank, ya bada mandir?" |
| Geo far from stated pincode | Map pin, one tap to confirm — no typing |

Language mirrors the script of the incoming address. Small touch, large effect on reply rate.

### Cedar — authorisation as policy

```cedar
// Contacting a customer is permitted only under all these conditions
permit(
  principal == Agent::"clarifier",
  action == Action::"contactCustomer",
  resource in OrderGroup::"pending_resolution"
) when {
  context.confidence < 0.80 &&
  context.messages_sent_for_order == 0 &&
  context.local_hour >= 9 && context.local_hour <= 20 &&
  context.channel == "sms"
};

// Never, under any confidence, for these
forbid(
  principal == Agent::"clarifier",
  action == Action::"contactCustomer",
  resource in OrderGroup::"opted_out"
);

// Auto-overwriting a stored address needs near-certainty
permit(
  principal == Agent::"resolver",
  action == Action::"overwriteStoredAddress",
  resource
) when {
  context.confidence > 0.95 &&
  context.geo_source == "landmark_graph"
};
```

A denial is **never a silent drop.** The case routes to the human review queue with the matched policy id and the denial reason attached. Cedar's decision, the policy id, and the full context are written to the audit log.

> **Twenty seconds of demo most teams cannot show:** set the clock to 23:00, submit a low-confidence address, and let Cedar return **DENY** on camera. Responsible automation, demonstrated rather than claimed. This also satisfies the "AWS open-source project" requirement independently of the cloud services.

**Implementation note.** Cedar's reference implementation is Rust with a Java SDK. Verify the Python path on Day 0. If it fights back, run Cedar as a separate small Lambda exposing `POST /authorize` — costs ~20 ms, saves three hours.

---

## 8. Data model

### 8.1 DynamoDB — single table `patasetu`

On-demand billing. Five access patterns, no scans.

| PK | SK | Entity | Serves |
|---|---|---|---|
| `ADDR#<sha256>` | `RESOLUTION` | Cached resolution | S0 cache probe — the main cost saver |
| `ORDER#<id>` | `META` | Order + status | Ops console lookup |
| `ORDER#<id>` | `EVENT#<ts>` | Audit trail | Full per-order timeline, sorted by time |
| `DIGIPIN#<cell>` | `NOTE#<ts>` | Rider access notes | "What do we know about this doorstep?" |
| `PIN#<pincode>` | `LOCALITY#<name>` | Pincode gazetteer | S1 validation and centroid lookup |

**GSI-1** (`status`, `created_at`) drives the review queue: every `NEEDS_INFO` case, oldest first, in one query.
**TTL** on cache items at 90 days.

### 8.2 OpenSearch — index `landmarks`

```json
{
  "settings": { "index.knn": true },
  "mappings": {
    "properties": {
      "canonical_name": { "type": "text",
        "fields": { "kw": { "type": "keyword" } } },
      "aliases":   { "type": "text" },
      "location":  { "type": "geo_point" },
      "embedding": { "type": "knn_vector", "dimension": 1024,
                     "method": { "name": "hnsw",
                                 "space_type": "cosinesimil",
                                 "engine": "faiss" } },
      "pincode":   { "type": "keyword" },
      "observation_count": { "type": "integer" },
      "confidence": { "type": "float" }
    }
  }
}
```

**OpenSearch Serverless** for the hackathon — no cluster sizing, no node management, scales to the volumes we will actually push. One fewer thing to debug at 2 am on Saturday.

### 8.3 S3 layout

```
s3://patasetu-<acct>-apsouth1/
├── audio/<order_id>/<ts>.webm        # rider voice notes
├── bulk/inbound/<job_id>.csv         # bulk ingest
├── bulk/results/<job_id>.jsonl
└── eval/<run_id>/                    # ablation outputs, plots
```

---

## 9. AWS service map

Every service earns its place. Nothing here is padding — judges notice services mentioned but never called.

| Service | Exact role | Why this and not something else |
|---|---|---|
| **Amazon Bedrock** (Nova Lite → Claude) | S3 field structuring; question generation; voice-note fact extraction | Managed, no GPU to provision, and the model cascade is trivial because both models sit behind one API |
| **Bedrock — Titan Text Embeddings v2** | 1024-d embeddings for landmark names, aliases, query text | Cheap per call, batchable, dimension-matched to the kNN index |
| **Strands Agents SDK** *(AWS open source)* | Clarification agent; rider-note extraction agent | AWS open-source SDK, defaults to the Bedrock provider, and the same agent code runs locally for Build It |
| **Cedar** *(AWS open source)* | Authorisation for outreach and for auto-overwriting addresses | Policy is data, not code — auditable, testable, reviewable without a deploy |
| **Amazon OpenSearch Serverless** | Landmark graph: BM25 + kNN + `geo_distance` in one query | The only managed store doing all three signals at once. Running three systems in four days is not viable |
| **Amazon DynamoDB** | Cache, order state, audit trail, rider notes, gazetteer | Single-digit-ms reads at the front of the pipeline; on-demand means zero idle cost |
| **AWS Lambda** | `resolver`, `clarifier`, `authorizer`, `learner`, `ingest` | Scales to zero between demo runs — exactly the spend profile a hackathon needs |
| **Amazon API Gateway** (HTTP API) | `/v1/resolve`, `/v1/feedback`, `/v1/notes`, `/v1/queue` | HTTP over REST API: lower latency, lower cost, and we need none of the REST extras |
| **AWS Step Functions** | Clarification workflow: authorise → send → wait → re-resolve → timeout → escalate | Wait-for-callback with timeout is native here |
| **Amazon EventBridge** | Event bus + scheduled graph compaction | Decouples learning from serving; adding a consumer needs no resolver change |
| **Amazon Location Service** | Geocoding fallback; map tiles for the console | Native IAM, no third-party key to leak in the repo |
| **Amazon Transcribe** | Rider voice notes, Hindi and English | Handles Indian-language audio; the rider must not type at a doorstep |
| **Amazon S3** | Audio, bulk CSV, evaluation artefacts | Event notifications trigger the learning path with no polling |
| **Amazon SNS** | Outbound clarification SMS | Simplest path to a working message |
| **AWS Amplify Hosting** | Ops console + rider PWA | Git push to a public URL in minutes; this console is our Best UI entry |
| **Amazon Cognito** | Auth for console and rider app | Judges open the URL; an unauthenticated admin console reads as careless |
| **Amazon CloudWatch** | Structured logs, custom metrics, one dashboard | The dashboard is a demo asset, not just plumbing |
| **AWS SAM + LocalStack** *(open source)* | Local dev and the Build It fallback | If cloud access breaks Saturday, the pipeline still runs and we still qualify |

---

## 10. Efficiency — latency, cascade, caching

### 10.1 Latency budget (p50, warm)

| Stage | Target | Note |
|---|---|---|
| S0 normalise + cache probe | ~8 ms | Cache hit ends the request here |
| S1 deterministic parse | < 1 ms | Pure CPU |
| S2 hybrid retrieval | ~45 ms | One OpenSearch call carrying all three signals |
| S3 LLM structuring | ~600 ms | Nova Lite; Claude escalation adds ~900 ms on a minority |
| S4 geocode | 0–120 ms | Zero on a landmark-graph hit |
| S5 DIGIPIN | < 1 ms | Local pure function |
| S6–S7 score and decide | ~2 ms | Arithmetic plus a lookup |
| **Total, cache miss** | **≈ 700 ms** | **Cache hit: ≈ 12 ms** |

### 10.2 The model cascade

```
        ┌──────────────────────────────┐
        │ S1 complete + valid pincode  │──▶ DONE, zero model cost
        │ + no landmark dependence?    │
        └──────────────┬───────────────┘
                       │ no
                       ▼
        ┌──────────────────────────────┐
        │      Amazon Nova Lite        │──▶ DONE (the large majority)
        └──────────────┬───────────────┘
                       │ escalate if:
                       │  • per-field confidence below threshold
                       │  • fields conflict with deterministic parse
                       │  • multi-script input
                       │  • alternatives[] non-empty
                       ▼
        ┌──────────────────────────────┐
        │           Claude             │
        └──────────────────────────────┘
```

Measure the escalation rate and report it. *"94% of addresses never touch the expensive model"* is a strong line, and it is the reason cost per address sits an order of magnitude below the naive design.

### 10.3 Caching, three levels

| Level | Key | Catches |
|---|---|---|
| Exact | `sha256(normalised)` | Literal repeats. Free, instant |
| Near-duplicate | embedding kNN, cosine > 0.97, same pincode | "Flat 4B Sunrise Apts" vs "4-B, Sunrise Apartment" |
| Landmark-level | `landmark_id` | Coordinates reused across every future address mentioning it — **this is the cache that compounds** |

### 10.4 Other deliberate choices

- **One OpenSearch round trip, not three.** BM25, kNN and geo filter travel in a single query body.
- **Batch embeddings** during corpus ingestion rather than one call per row.
- **Provisioned concurrency = 1** on the resolver for the ten minutes around the demo recording. Cold starts have ruined more hackathon videos than bugs have.
- **ARM64 (Graviton) Lambdas** — cheaper per millisecond, no code change for pure Python.
- **Bulk mode is async.** CSV → S3 → EventBridge fan-out → progress in the console. Never hold an HTTP connection open for 5,000 addresses.

---

## 11. Frontend architecture

**Stack:** React 18 + Vite + TypeScript · Tailwind · MapLibre GL JS (native fit for Amazon Location tiles) · Recharts · Cognito auth · Amplify Hosting.

### 11.1 Ops console (desktop)

| Screen | Contents |
|---|---|
| **Playground** | Textarea → paste a messy address. Left: raw text with spans highlighted by field. Right: structured output, DIGIPIN, map pin, confidence bar, `evidence[]` list. **Build this first — the video opens on it.** |
| **Review queue** | Every `NEEDS_INFO` / `AMBIGUOUS` case, oldest first, with the reason confidence is low and the proposed question. One-click approve or edit |
| **Map view** | Resolved addresses as pins coloured by confidence; landmarks as a separate layer; click a DIGIPIN cell for its access notes |
| **Landmark explorer** | Search the graph — aliases, observation count, coordinates, rider notes |
| **Metrics** | Resolution rate, clarification rate, p95, cost per address, ablation table, learning curve |

The playground's **stage-by-stage reveal** — deterministic fields appearing, then landmarks matching, then the pin dropping, then the DIGIPIN — is the most persuasive 20 seconds available.

### 11.2 Rider PWA (mobile)

Deliberately tiny. Four things:

1. Address card: map pin, DIGIPIN, existing access notes
2. One large hold-to-record microphone button → presigned S3 upload
3. Two outcome buttons: *Delivered* / *Couldn't find*
4. Works offline for reads (DIGIPIN is computable client-side with no network)

PWA, not native — installable from a URL, no app store, works on any phone at the venue.

### 11.3 The contract

`POST /v1/resolve`'s response shape is **frozen on Thursday morning** so the frontend can build against a mock while the backend is still being written. That single decision is the difference between four people working in parallel and four people blocking each other.

---

## 12. Evaluation harness

Judges score idea, AWS usage, learning, execution and the demo. An evaluation harness feeds four of those five, and virtually no competing team will have one.

### 12.1 Corpus

1. **~120 real addresses** collected from friends, family, hostel-mates, with consent, anonymised. One evening in a WhatsApp group — the highest-value hour of the whole hackathon.
2. **~2,000 generated** by perturbing those seeds: drop the pincode, misspell the landmark, transliterate to Devanagari, abbreviate, reorder fields, merge lines, inject a phone number, append "call before coming".
3. **300-address gold set**, hand-labelled with correct fields and true coordinates. Split 150 dev / 150 test. **Touch the test split exactly once, at the end.**

### 12.2 Metrics

| Metric | Target | Why it's in the set |
|---|---|---|
| Field-level F1 | > 0.90 | Per-field, so we see *which* field is weak |
| Full-address exact match | > 0.75 | Strict, unforgiving, honest |
| Median geocode error | < 150 m | Haversine to true point. **Median**, not mean — outliers dominate the mean |
| Auto-resolution rate | > 80% | Fraction needing no human or customer input |
| Clarification rate | < 15% | Every question is friction for a real customer |
| Precision @ threshold | > 95% | Of those auto-resolved, how many are right — the safety metric |
| p95 latency | < 1.2 s | Dispatch-time budget |
| Cost per address | tracked | Proves the cascade and cache work |

### 12.3 Ablation table

Same 150-address test split, progressively richer configurations:

| Configuration | Field F1 | Geo error | Clarify rate |
|---|---|---|---|
| A — Deterministic regex only | — | — | — |
| B — A + single LLM call, no retrieval | — | — | — |
| C — B + BM25 landmark retrieval | — | — | — |
| D — C + vector retrieval + geo filter | — | — | — |
| E — D + warmed landmark graph *(full system)* | — | — | — |

**Configuration B is the entire product most other teams will submit.** Showing the gap from B to E is the argument that our architecture was necessary rather than decorative.

### 12.4 Learning curve

Resolution rate vs addresses seen per locality. One chart, no explanation needed.

---

## 13. Local and Build It mode

Everything switches through one module and one environment variable.

```python
# layers/common/providers.py
PROVIDER = os.environ.get("PROVIDER", "aws")   # "aws" | "local"
```

| Concern | `aws` | `local` |
|---|---|---|
| Model | Bedrock (Nova Lite / Claude) | Ollama via Strands' model-provider switch |
| Embeddings | Titan Text Embeddings v2 | `sentence-transformers` local model |
| Search | OpenSearch Serverless | OpenSearch in Docker |
| KV store | DynamoDB | DynamoDB Local (LocalStack) |
| Geocoding | Amazon Location Service | Pincode centroid only |
| Compute | Lambda | SAM Local |
| DIGIPIN | pure function | **identical** — no network either way |
| Cedar | Lambda authorizer | in-process |

Write this abstraction on **Day 1, not Day 3.** It is what makes the Build It fallback a configuration change rather than a rewrite.

---

## 14. Observability

- **Structured JSON logs** with a `correlation_id` propagated across every stage. Each stage logs its input hash, duration, and decision.
- **Custom CloudWatch metrics:** `ResolutionRate`, `ClarificationRate`, `EscalationRate`, `CostPerAddress`, `CacheHitRate`, `GeoSourceDistribution`, stage-level latencies.
- **One dashboard** carrying all of the above. Put it on screen in the video — it is a demo asset.
- **Audit trail** in DynamoDB: every resolution and every Cedar decision, queryable as a per-order timeline.

---

## 15. Failure modes and fallbacks

| Failure | Behaviour |
|---|---|
| Bedrock throttled or unavailable | Fall back to deterministic-only result with confidence capped at 0.5 and status `NEEDS_INFO`. Never 500 |
| OpenSearch unavailable | Skip S2; proceed with deterministic + geocoder. Log degraded mode; penalise confidence |
| Location Service fails | Fall back to pincode centroid, mark `source: pincode_centroid` |
| Cedar evaluation errors | **Fail closed.** No message sent; case goes to the human queue |
| Transcribe fails on a voice note | Store the raw audio, mark the note unprocessed, retry on schedule |
| Model returns invalid JSON | One retry with a stricter reminder prompt; then fall back to deterministic-only |
| Cold start on the demo path | Provisioned concurrency during the recording window; warm the path before hitting record |

---

*Service availability, model access and pricing should be verified in the AWS console on Day 0, as these change independently of this document.*
