# PataSetu — How It Works (interview explainer)

This is the version you say out loud. Simple words, real numbers, and the
"why" behind each decision. Read it twice and you can explain the whole
system on a whiteboard in five minutes.

---

## 1. The one-line pitch

> "PataSetu takes a messy Indian delivery address — like *'h no 14 behind shiv
> mandir near gupta store ramesh nagar delhi 110015'* — and turns it into a
> clean structured address, a map coordinate, a DIGIPIN code, and a confidence
> score. If it is genuinely unsure, it asks the customer **one** short question
> instead of guessing."

---

## 2. The problem, in plain words

In India, an address is often not an address. It is **directions for a human**:
"behind the temple, near Gupta's shop". Many areas have no proper street
numbers, so this is normal, not lazy.

That causes three costs for a delivery company:

1. The rider **phones the customer** on almost every order.
2. The **first attempt fails** — fuel, time, a second trip.
3. What the rider learns ("gate 3, ask the guard") **dies in their head**. The
   next rider starts from zero.

So the system has to *work with* landmarks, not fight them.

---

## 3. The journey of one request (the data flow)

A client sends one HTTP call:

```
POST /v1/resolve
{ "raw": "h no 14 behind shiv mandir near gupta store ramesh nagar delhi 110015 call before coming 9876543210" }
```

It goes to **API Gateway → one Lambda function (the "resolver")**. Inside that
Lambda the text passes through **eight small stages, S0 to S7**, in order.
Think of it as an assembly line. Cheap stages run first; the expensive AI
stage runs only when needed.

### S0 — Normalise (free, ~1 ms)

Clean the text so the same address always looks the same.

- Lowercase, remove junk punctuation, collapse spaces.
- Expand short forms from a table of 158 entries: `h no` → `house number`,
  `opp` → `opposite`, `blr` → `bengaluru`.
- If the text is in **Hindi (Devanagari)**, make a Latin transliteration and
  keep **both** — `शिव मंदिर` and `shiv mandir` — so either spelling matches.
- Remove chatter like "call before coming".
- Make a **hash** of the cleaned text. This hash is the **cache key**.

**Cache check:** if we have seen this exact address before, return the stored
answer in ~12 ms. Nothing else runs. In a real city many addresses repeat
(same buildings, same hostels), so this is the biggest cost saver.

### S1 — Deterministic parse (free, <1 ms)

Pull out everything that plain rules can settle. No AI here.

- **Phone number → removed first**, before anything else, and stored
  separately. It must never reach an AI model or a log. (This is a privacy
  rule, not a nice-to-have.)
- **Pincode** `110015` → looked up in our **gazetteer** (a table of 19,300
  real pincodes built from India Post's official directory). That gives us
  locality, district, state, and a rough map centre for free.
- Check the locality in the text agrees with the pincode. If the text says
  "Bengaluru" but the pincode is Delhi, that is a **conflict** — we flag it
  and lower confidence; we never silently pick a side.
- House / flat number, floor, tower, sector.
- **Landmark phrases** with their relation: *behind* Shiv Mandir, *near* Gupta
  store. The relation word matters — "behind" and "opposite" are different
  doorsteps. Works in both word orders: English "behind X" and Hindi
  "X ke pichhe".

For a clean address, this stage alone finishes the job — **zero AI cost**.

### S2 — Retrieve landmark candidates (~45 ms)

Search our **landmark index** for places matching the landmark phrases,
using **three signals in one round trip**:

| Signal | What it catches |
|---|---|
| **BM25** (word search) | exact names: "shiv mandir" |
| **Vector search** (embeddings) | misspellings and short forms: "shiv mandhir", "sunrise apts" |
| **Geo filter** | only places near the pincode — this removes the 400 other Shiv Mandirs in India |

The three ranked lists are combined with **Reciprocal Rank Fusion (RRF)**:
each list gives a place `1 / (60 + rank)` points; add them up. A place found by
all three signals beats a place found by one. We chose RRF because it needs no
tuning and works fine when a signal is missing (e.g. no pincode → no geo).

One important trick: the **locality itself** ("Ramesh Nagar") is also searched
as the *anchor*. "X, near Y" means the doorstep is **at X**, not at Y.

### S3 — AI structuring (~600 ms, only when needed)

Now — and only now — an LLM sees the text. It gets the text **plus** the
fields S1 already settled **plus** the candidate landmarks from S2. Its job is
narrow: assign leftover words to fields and pick landmarks **by id from the
list we gave it**.

Five hard rules in the prompt:
1. Output only JSON.
2. `null` for anything not in the text. **Never guess.**
3. Landmark ids must come from our candidate list. Never invent one.
4. Give a confidence per field.
5. If two readings are equally plausible, return **both**.

**The model cascade** (cost control):
- Address complete after S1 with a valid pincode? → **no model call at all**.
- Otherwise → **Amazon Nova Lite** (cheap). Handles most addresses.
- Escalate to **Claude** (expensive) only if: a field has low confidence, the
  model contradicts the deterministic parse, the input mixes scripts, or the
  model returned alternatives.

We validate what the model returns: it cannot overwrite a verified pincode,
cannot invent landmark ids, and bad JSON gets exactly **one retry**, then we
fall back to the S1 result. Never a loop, never a 500 error.

### S4 — Geocode (0–120 ms)

Get a coordinate, from the best source first:

1. **Landmark graph** — a matched landmark has a known coordinate. Free,
   instant, and the most accurate because it came from real places.
2. **Amazon Location Service** — a street geocoder, if no landmark matched.
3. **Pincode centroid** — the middle of the pincode area. Last resort, and it
   is **labelled** as such so the UI never draws it as a doorstep pin.

Every coordinate carries its `source` and an honest `accuracy_m`.

### S5 — DIGIPIN (free, <1 ms)

DIGIPIN is India Post's national grid: the country divided into ~4 m × 4 m
cells, each with a 10-character code. It is a **pure function of
(latitude, longitude)** — no API, no cost, works offline on a rider's phone.
We vendored the official encoder and test it against India Post's own
reference value.

Bonus: two differently-worded addresses that land in the same 4 m cell are
provably the same doorstep. That is our deduplication key.

### S6 — Confidence (~1 ms)

Seven features → one score in [0, 1]:

field completeness · pincode/locality agreement · landmark match strength ·
how many times that landmark has been confirmed · geo source quality ·
whether two candidates are fighting · the model's own confidence (weighted
lightly — LLMs are over-confident).

Then a **calibration step** (isotonic regression, fitted on held-out data)
maps the raw score to a real probability, so "0.9" actually means "right about
90% of the time". Until it is fitted, the response says "uncalibrated" openly.

### S7 — Decide

Three possible outcomes, no fourth:

| Status | When | What happens |
|---|---|---|
| `RESOLVED` | confidence ≥ threshold | return the answer |
| `NEEDS_INFO` | below threshold | ask **one** question about the single most valuable missing field, in the customer's script (Hindi in → Hindi out) |
| `AMBIGUOUS` | two landmarks the text names equally well, far apart | return **both**; a human picks. Never guess. |

The response also carries an `evidence[]` list — plain-English reasons for
every decision — so an operator can see *why*.

Finally: if `RESOLVED`, store it in the cache; write an audit row; if
`NEEDS_INFO` or `AMBIGUOUS`, it appears in the **review queue** for a human.

---

## 4. The pieces and why each one is there

```
Console (React) ──► API Gateway ──► Lambda "resolver" (S0–S7)
                                        │
            ┌─────────────┬─────────────┼──────────────┬──────────────┐
         DynamoDB     OpenSearch      Bedrock       Location       DIGIPIN
         cache,       landmark        Nova Lite /   Service        (pure
         audit,       index:          Claude /      geocoder       function,
         queue        BM25+kNN+geo    Titan embed                  no service)
```

| Piece | Why this one |
|---|---|
| **Lambda + API Gateway (HTTP API)** | scales to zero between demos; pay per request |
| **DynamoDB** (one table) | single-digit-ms reads for the cache; a secondary index gives the review queue in one query, no scans |
| **OpenSearch Serverless** | the only managed store that does word search + vector search + geo filter in **one** query |
| **Bedrock** | both models behind one API, so the cheap→expensive cascade is trivial |
| **Amazon Location** | geocoder fallback with IAM auth, no third-party key to leak |
| **Cognito** | login for the review queue; the playground stays public for judges |
| **CloudWatch** | every request emits its metrics inside a log line (Embedded Metric Format) — no extra API call, no added latency |
| **Amplify Hosting** | the React console, git-push to a URL |

**Local mode:** one environment variable, `PROVIDER=local`, swaps every cloud
service for an in-process version — a real BM25/vector/geo search engine in
pure Python, a dictionary instead of DynamoDB, Ollama instead of Bedrock. The
whole pipeline and all **376 tests run with no internet**.

---

## 5. Where the data came from (say this — interviewers ask)

We did **not** scrape people's addresses. We used the **All-India Pincode
Directory** — India Post's public list of 157,000 post offices, each with a
real name, real pincode and a published latitude/longitude.

- **Real** Indian place names and pincode pairings.
- **Ground-truth coordinates**, so we can measure geocode error in metres.
- **Zero personal data**, by construction.

The trade-off, stated honestly: post office addresses are cleaner than real
customer addresses. So we keep 120 clean "seed" addresses and a generator adds
the mess — dropped pincodes, misspellings, Hindi transliteration, shuffled
fields, fake phone numbers, "call before coming" — producing 2,040 test
addresses. The generator **labels what it changed**, so the answer key stays
correct.

That directory also had junk in it — a literal `TEST OFFICE` at pincode
`999999`, offices plotted 700 km inside the wrong state — which we found by
testing and filtered out.

---

## 6. How we know it works (the evaluation)

A 300-address gold set (150 dev / 150 test — the test half is touched **once**,
at the end). Seven metrics, because one number hides trade-offs:

field F1 · exact-match rate · **median** geocode error (median, because a few
huge misses would dominate a mean) · auto-resolution rate · clarification rate
· precision of what we auto-resolve · p95 latency.

And an **ablation table**: the same code with stages switched on one at a time.

| Configuration | Geo median error |
|---|---|
| A — rules only, no retrieval | 969 m |
| + landmark retrieval (BM25) | **0 m** |
| + vector + geo signals | **0 m** |

"Configuration B — just one LLM call on the raw text — is the entire product
most teams would build. The gap from B to the full system is the argument that
the architecture was necessary."

---

## 7. Five design decisions to say out loud

1. **The LLM is stage three, not stage one.** Cheap rules first; most addresses
   never touch the expensive model. ~10× cheaper per address.
2. **Never guess.** `null` is a valid answer. A confidently wrong flat number
   fails silently at the doorstep — worse than an admitted gap.
3. **Learning never sits in the request path.** Graph updates from confirmed
   deliveries happen asynchronously (EventBridge → learner Lambda).
4. **Uncertainty is a first-class output.** Confidence is calibrated against
   held-out data and shown with a reliability diagram, not asserted.
5. **The landmark graph compounds.** It is keyed on places and DIGIPIN cells,
   not on address strings, so what we learn from customer A's badly written
   address helps customer B in the same building.

---

## 8. Good stories for "what was hard?"

- **A 3 km geo radius is a city assumption.** 29% of localities in our test set
  sit more than 3 km from their own pincode's centre (rural pincodes are
  huge). The filter was throwing away the right answer. Fix: scale the radius
  to the pincode's size. Median error went from ~1 km to 0 m.
- **"Near X" is not "at X".** We were geocoding *from the landmark*. "Post
  Office, near Nairi" is at the post office. Fix: search the locality itself
  as the anchor.
- **A quarter of results came back AMBIGUOUS.** RRF ranks 1 and 2 always
  score 1/61 vs 1/62 — a 1.6% gap — whatever the names are. Fix: judge rivalry
  on *name similarity*, not rank. Dropped to 4%.
- **Our own metric flattered us.** Stratified sampling made the test set 37%
  clean rows vs 7.6% in the corpus. Fixed, and a test now guards it.
- **Hindi word order.** "behind shiv mandir" vs "shiv mandir ke pichhe" — the
  relation word moves. Matching only the English order captured the wrong span.
- **A backspace character** (`\b` mangled through shell quoting) silently
  disabled a regex for a day. Now we grep for control characters after any
  scripted edit.

---

## 9. Likely questions, short answers

**Why not just call GPT on the address?**
Cost and trust. Rules resolve many addresses for free; the model runs only on
the hard ones, with settled fields and a candidate list already in the prompt.
And a model alone cannot *place* an address — retrieval against a landmark
graph is what turns "behind shiv mandir" into a coordinate.

**Why RRF instead of a weighted score?**
BM25 scores are unbounded, cosine is [-1,1], distance is metres. Blending
them needs normalisation constants you must re-tune every time the data
changes. RRF uses only ranks, so it needs none — and if a signal is missing it
just sums the rest.

**Why DIGIPIN?**
Pure function of lat/lng: no API, no cost, offline. And a 4 m cell is a
natural deduplication key across differently-worded addresses.

**How do you handle a phone number in the address?**
Stripped in S1 before any model, embedding, log, or cache sees it. A smoke
test asserts the number appears nowhere in the response.

**What if Bedrock is down?**
The request degrades to the rules-only answer with confidence capped, status
`NEEDS_INFO`, and the reason in `evidence[]`. Never a 500.

**How does it get better over time?**
Confirmed deliveries increment a landmark's observation count and nudge its
coordinate; customer answers become ground truth; rider notes attach to the
DIGIPIN cell. The graph learns the neighbourhood.

**What is the privacy story?**
Seed data is public institutional addresses; phone numbers never reach a
model; raw address text is never logged; the console's queue is behind Cognito.

---

## 10. Draw this on the whiteboard

```
 messy text
     │
     ▼
 S0 normalise ──► cache hit? ──► done (12 ms)
     │ miss
     ▼
 S1 rules: strip phone · pincode → gazetteer · house no · landmark phrases
     │
     ▼
 S2 retrieve: BM25 + vector + geo  ──► RRF fusion ──► candidates
     │
     ▼
 S3 LLM (only if needed): fill leftover fields, pick landmark ids from the list
     │
     ▼
 S4 geocode: landmark graph → street geocoder → pincode centre (labelled)
     │
     ▼
 S5 DIGIPIN (pure function)
     │
     ▼
 S6 confidence (7 features, calibrated)
     │
     ▼
 S7 decide: RESOLVED │ NEEDS_INFO (one question) │ AMBIGUOUS (both readings)
     │
     ▼
 response + evidence[] · cache · audit · review queue
```

Say it in this order, point at each box, and give the one number for each:
cache 12 ms, rules <1 ms, retrieval 45 ms, model 600 ms, total ~700 ms.
