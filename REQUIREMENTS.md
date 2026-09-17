# Requirements — PataSetu

Scope: a four-day hackathon build. Requirements are written so that each one is either demonstrably met on Sunday or explicitly cut. Anything that cannot be verified is not a requirement — it is a wish.

Priority key: **P0** must ship or the project fails · **P1** should ship, strongly expected · **P2** nice to have, first on the kill list.

---

## 1. Users

| User | Needs |
|---|---|
| **Operations analyst** | Resolve a batch of addresses; review the ones the system is unsure about; correct mistakes; see whether the system is getting better |
| **Delivery rider** | Know where the doorstep actually is before arriving; read what previous riders learned; leave a note without typing |
| **Customer** | Not be interrogated. At most one short question, in their own language, only when genuinely necessary |
| **Judge / evaluator** | Open a public URL, paste a messy address, and see it work — without a login wall or a setup step |

---

## 2. Functional requirements

### 2.1 Core resolution

| ID | Priority | Requirement |
|---|---|---|
| FR-01 | P0 | Accept a free-text address string and return a structured address with the fields: `building`, `street`, `sub_locality`, `locality`, `city`, `district`, `state`, `pincode`, `landmarks[]` |
| FR-02 | P0 | Return `null` for any field not present in the input. The system must never invent a value |
| FR-03 | P0 | Return a latitude/longitude with an explicit `source` of `landmark_graph`, `geocoder`, or `pincode_centroid` |
| FR-04 | P0 | Return a valid DIGIPIN computed from the resolved coordinates |
| FR-05 | P0 | Return a confidence score in `[0,1]` and a status of `RESOLVED`, `NEEDS_INFO`, or `AMBIGUOUS` |
| FR-06 | P0 | Return an `evidence[]` array of human-readable strings explaining why each major decision was made |
| FR-07 | P1 | Accept an optional GPS `hint` and use it to constrain candidate retrieval |
| FR-08 | P1 | When two readings are equally plausible, return both in `alternatives[]` and set status to `AMBIGUOUS` rather than silently picking one |
| FR-09 | P1 | Accept input in Devanagari and in Latin transliteration, and resolve both to the same result |
| FR-10 | P2 | Accept a CSV of addresses via S3 and process asynchronously with a job status endpoint |

### 2.2 Landmark graph

| ID | Priority | Requirement |
|---|---|---|
| FR-11 | P0 | Maintain a searchable landmark index with canonical name, aliases, coordinates, pincode, and observation count |
| FR-12 | P0 | Match a landmark mentioned in an address against the index using lexical, vector, and geographic signals combined |
| FR-13 | P1 | On a confirmed delivery, increment the landmark's observation count and nudge its coordinates toward the confirmed point |
| FR-14 | P1 | Store free-text access notes ("gate 3, ask the guard") against a DIGIPIN cell and surface them for any future address resolving to that cell |
| FR-15 | P2 | Accept a rider voice note, transcribe it, extract structured access facts, and attach them to the landmark |

### 2.3 Clarification

| ID | Priority | Requirement |
|---|---|---|
| FR-16 | P0 | When confidence is below threshold, generate exactly one question targeting the single field that would most increase confidence |
| FR-17 | P0 | Generate the question in the script of the incoming address — Devanagari in, Hindi out |
| FR-18 | P1 | Accept a customer's answer, re-run resolution with it, and update status |
| FR-19 | P1 | Time out an unanswered question and escalate the case to the human review queue |
| FR-20 | P2 | Send the question over SMS via SNS |

### 2.4 Authorisation and safety

| ID | Priority | Requirement |
|---|---|---|
| FR-21 | P0 | Every outbound customer contact must be authorised by a Cedar policy evaluation before it is sent |
| FR-22 | P0 | Cedar must deny contact outside 09:00–20:00 local time, deny a second message for the same order, and deny any contact to an opted-out customer |
| FR-23 | P0 | A Cedar denial must route the case to the human review queue with the matched policy id and denial reason attached, never silently drop it |
| FR-24 | P1 | Overwriting a stored customer address automatically requires confidence > 0.95 **and** `geo_source == landmark_graph` |
| FR-25 | P0 | Phone numbers must be stripped at stage S1, stored in a separate field, and never included in text sent to a model or to the embedding API |

### 2.5 Ops console

| ID | Priority | Requirement |
|---|---|---|
| FR-26 | P0 | A **playground** screen: paste an address, see the structured output, map pin, DIGIPIN, confidence, and evidence |
| FR-27 | P0 | A **review queue**: every `NEEDS_INFO` and `AMBIGUOUS` case, oldest first, with one-click approve or edit |
| FR-28 | P1 | A **map view**: resolved addresses as pins coloured by confidence, with a separate landmark layer |
| FR-29 | P1 | A **metrics** screen: resolution rate, clarification rate, p95 latency, cost per address, ablation table, learning curve |
| FR-30 | P2 | A **landmark explorer**: search the graph, view aliases, observation count, and rider notes |

### 2.6 Rider app

| ID | Priority | Requirement |
|---|---|---|
| FR-31 | P1 | Show the address card with map pin, DIGIPIN, and any existing access notes |
| FR-32 | P1 | A single hold-to-record microphone button that uploads audio to S3 — no typing required |
| FR-33 | P1 | Two outcome buttons: *Delivered* and *Couldn't find*, both of which emit a learning event |
| FR-34 | P2 | Installable as a PWA and usable with intermittent connectivity |

### 2.7 Evaluation

| ID | Priority | Requirement |
|---|---|---|
| FR-35 | P0 | A generated corpus of ≥ 2,000 addresses, seeded from ≥ 100 consented real addresses |
| FR-36 | P0 | A hand-labelled gold set of ≥ 300 addresses, split 50/50 into dev and test |
| FR-37 | P0 | A reproducible ablation runner producing the five-configuration table (A through E) |
| FR-38 | P0 | Confidence calibration fitted on the dev split, with a reliability diagram |
| FR-39 | P1 | A learning-curve plot: resolution rate versus addresses seen per locality |
| FR-40 | P0 | The test split must be evaluated exactly once, at the end |

---

## 3. Non-functional requirements

### 3.1 Performance

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | p50 latency, cache miss | ≤ 800 ms |
| NFR-02 | p50 latency, cache hit | ≤ 20 ms |
| NFR-03 | p95 latency, cache miss | ≤ 1.2 s |
| NFR-04 | Retrieval must use a single OpenSearch round trip carrying all three signals | 1 call |
| NFR-05 | Bulk ingest must not hold an HTTP connection open | async only |

### 3.2 Cost

| ID | Requirement |
|---|---|
| NFR-06 | Total spend across the event must stay under the team's AWS credit allocation |
| NFR-07 | An AWS Budgets alert at $25 must be configured on Day 0 |
| NFR-08 | Addresses fully resolved by the deterministic stage must incur **zero** model cost |
| NFR-09 | Escalation rate to the expensive model must be measured and reported |
| NFR-10 | Cost per resolved address must be emitted as a CloudWatch metric |
| NFR-11 | The OpenSearch Serverless collection must be torn down overnight, or the idle cost knowingly accepted |

### 3.3 Reliability and correctness

| ID | Requirement |
|---|---|
| NFR-12 | The DIGIPIN encoder must be vendored from the official implementation, not hand-rolled, and unit-tested against known coordinate pairs from the India Post portal |
| NFR-13 | The model must be prompted with a strict JSON schema and must return `null` rather than guess |
| NFR-14 | A pincode–locality conflict must hard-penalise confidence, never be silently resolved in favour of one side |
| NFR-15 | A `pincode_centroid` geocode must never be presented as a doorstep-accurate location |

### 3.4 Security and privacy

| ID | Requirement |
|---|---|
| NFR-16 | The ops console must be behind Cognito auth. The playground may be public and read-only |
| NFR-17 | No credentials, API keys, or `.env` files in the repository |
| NFR-18 | S3 buckets private; rider audio accessed only via presigned URLs |
| NFR-19 | All demo data anonymised; real seed addresses used with consent and stripped of names |

### 3.5 Operability

| ID | Requirement |
|---|---|
| NFR-20 | Structured JSON logs with a correlation id propagated across every stage |
| NFR-21 | One CloudWatch dashboard carrying all headline metrics — this is a demo asset, not just plumbing |
| NFR-22 | Every resolution writes an audit event to DynamoDB, queryable as a per-order timeline |
| NFR-23 | Infrastructure defined entirely in SAM; no console-clicked resources |

### 3.6 Portability

| ID | Requirement |
|---|---|
| NFR-24 | Cloud and local providers must be switchable via a single module (`providers.py`) and one environment variable |
| NFR-25 | The full pipeline must run offline on LocalStack + local OpenSearch + Ollama, for the Build It track |

---

## 4. Hackathon compliance requirements

These are not product requirements but they are pass/fail for the event.

| ID | Requirement |
|---|---|
| HR-01 | Every team member has a WeMakeDevs account and a **verified** AWS Builder Center student profile — registration is incomplete without it |
| HR-02 | Team size is 1–4 |
| HR-03 | At least one AWS open-source project or AWS service is used — satisfied several times over (Strands, Cedar, OpenSearch, SAM; plus the cloud services) |
| HR-04 | Project work begins only when the clock starts. Learning and practice beforehand are fine |
| HR-05 | A 3-minute recorded demo video is submitted. There is no live demo — the video is what judges see |
| HR-06 | The submission states explicitly what the team learned, since this is scored |
| HR-07 | A blog post is published on AWS Builder Center and linked in the submission |
| HR-08 | Submission is filed at least two hours before the deadline |

---

## 5. Acceptance criteria — the Sunday checklist

The project is done when every line below is true. Not before, and not "nearly".

- [ ] A public URL resolves a pasted messy address end-to-end, in under 2 seconds, with no login
- [ ] The returned DIGIPIN matches the India Post portal for the same coordinates
- [ ] A low-confidence address produces exactly one targeted question, in the right language
- [ ] With the clock set to 23:00, Cedar returns **DENY** on screen and the case appears in the review queue with the reason
- [ ] The ablation table is filled in with real numbers for all five configurations
- [ ] The reliability diagram shows calibration close to the diagonal
- [ ] The learning curve rises visibly as a locality is fed in
- [ ] The CloudWatch dashboard shows p95 latency, resolution rate, and cost per address
- [ ] The 3-minute video is recorded, watched back once in full, and re-recorded if it dragged
- [ ] The blog post is live on AWS Builder Center and linked
- [ ] README states the synthetic-data limitation openly
- [ ] Every team member can explain the confidence calibration if asked

---

## 6. Explicitly out of scope

Stating these prevents Saturday-night scope creep.

- Real-time route optimisation or rider dispatch
- Integration with any real courier or e-commerce system
- Address autocomplete as the user types
- Multi-tenant accounts, billing, or rate-limit tiers
- Any script beyond Latin and Devanagari as a *measured* capability
- Mobile native apps (the rider app is a PWA)
- Training or fine-tuning any model

---

## 7. Kill list — decided Thursday, obeyed Saturday

If behind schedule on Saturday evening, drop in this order:

1. **FR-15, FR-32** — rider voice notes and Transcribe
2. **FR-19, FR-20** — the Step Functions wait-for-reply branch and SMS. Return the question in the API response and collect the answer in the console instead
3. **FR-10** — bulk CSV ingest
4. **FR-30** — landmark explorer screen

**Never dropped:** the evaluation harness (FR-35 to FR-40), the Cedar denial demo (FR-21 to FR-23), the playground (FR-26), and the review queue (FR-27).
