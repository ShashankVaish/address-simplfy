# Learnings

Written as we go, not reconstructed on Sunday. The judging criteria reward this
explicitly, and a note written three days later is a summary rather than a
learning.

---

## Day 1 — Thursday 17 September

### The DIGIPIN format has changed, and most secondary sources are stale

Every blog post, wrapper library and explainer describes DIGIPIN as
`XXX-XXX-XXXX` — three groups, two hyphens. The **official India Post
implementation no longer emits hyphens.** The current revision of
`src/digipin.js` (Department of Posts / CEPT, dated 2026-05-04) produces a
continuous 10-character string, and its decoder explicitly *rejects* any input
containing hyphens or spaces:

```js
if (level === 3 || level === 6) digiPin += "";   // was "-"
```

Two consequences we acted on:

- The canonical stored and transmitted value is the **continuous form**.
  Hyphenation is a display concern only, handled by `format_digipin`, and the
  parser accepts both so a user can paste back what they were shown.
- The repository was also **moved**: `CEPT-VZG/digipin` is archived and points
  to `INDIAPOST-gov/digipin`. Vendoring from the old URL would have silently
  pinned us to the beta alphabet.

This is precisely the failure the architecture document warned about — a
hand-rolled or stale encoder produces a *well-formed* code for the wrong
doorstep, and nothing raises. Our `tests/test_digipin.py` asserts against the
official reference vector `getDigiPin(13.11179621, 80.20264269) == "4T396F42L7"`
rather than against our own output, which is the only version of this test worth
having.

### "Real data" was available, and better than what we planned to collect

The plan was 120 addresses from WhatsApp groups. We used the **All-India Pincode
Directory** (India Post, via data.gov.in) instead — 157,126 publicly listed post
offices with real names, real localities, real pincodes and published
coordinates. Three advantages that were not obvious beforehand:

1. **It carries ground truth.** Every record has a coordinate, so the gold set
   has a true point to measure geocode error against. Addresses collected from
   friends would have given us text but no trustworthy coordinate — and
   median-geocode-error is meaningless without one.
2. **No personal data at all.** NFR-19 and the README's privacy limitation are
   satisfied *by construction* rather than by an anonymisation pass we would
   have to trust. We never had to hold anyone's home address.
3. **The landmarks are real too.** Named places near a given office, found by
   spatial search, are real entities with real coordinates — so the landmark
   graph can be warmed with real geometry instead of invented points.

The honest trade-off, which belongs in the README: post office addresses are
*better formed* than the landmark-based addresses PataSetu exists to handle. So
the seeds are clean by design and **all** the mess is introduced by
`corpus_gen.py`, which knows what it perturbed and can label it. Real address
*content*, modelled *messiness*.

### Official government data contains junk, and it validates silently

Found only because a test asserted that `999999` could not possibly resolve:

| Record | Problem |
|---|---|
| `999999 TEST OFFICE` | A literal test row, filed under the Tamilnadu circle with a Telangana district and Tamil Nadu coordinates |
| `900056`, `900099` | Army Postal Service pincodes whose stated district and coordinates contradict each other |
| ~800 rows | Coordinates rounded to 2–3 decimals, i.e. 110 m–1.1 km of error |
| 1,255 rows | Offices plotted outside their own stated state — pincode 683545 is in Kerala, its office sits in Karnataka, 700 km away |
| 3,850 rows | Offices far from their own district centre, e.g. an Indore-district office at 81°E, 540 km east |
| 1,950 names | Postal jargon left in the locality name: `Aliganj SO South Delhi` |

The dangerous one is the first. Importing it made `999999` a *valid* pincode:
any address containing those digits would have been validated, assigned a
Telangana centroid, and had a confident wrong state propagated through every
later stage. Nothing would have raised.

The lesson is about *where* validation belongs. A format check is not a
validity check. Our coordinate-precision filter counted decimal *places*, which
the directory defeats by storing placeholders at full width — `25.250000` looks
like six-decimal precision and carries a kilometre of error. Catching it needed
a check on the **value** (does it land exactly on a 0.01° multiple in both
axes?) rather than on the string. Similarly, catching the misplaced offices
needed each record checked against a *robust aggregate of its peers* — state
percentile boxes and district medians — not against a fixed threshold.

### Regex alternation precedence cost us an hour of confusion

```python
re.compile(rf"(?<!\w){alternation}(?!\w)")     # wrong
re.compile(rf"(?<!\w)(?:{alternation})(?!\w)") # right
```

Alternation binds *looser* than concatenation, so the first pattern applies the
word-boundary guards only to the first and last branches and lets everything in
between match mid-word. With a 158-entry abbreviation table the results were
memorable: the `up` branch rewrote **"gupta" → "guttar pradeshta"**, `beh`
rewrote **"behind" → "behindind"**, and `st` rewrote **"store" → "streetore"**.

It was obvious once seen and invisible until then, because each individual
expansion is correct — only the boundary handling is wrong. The regression test
now asserts a list of words that must pass through untouched.

### Transliteration is mostly about the vowel that is not written

A character-for-character Devanagari map produces `मंदिर → mndira`, which
matches nothing in a Latin index. Three rules did almost all the work:

1. A matra replaces the inherent vowel; a **virama** cancels it.
2. An **anusvara does not** cancel it — `मं` is "man", not "mn". Treating the
   nasal marks as suppressors was the original bug.
3. **Word-final schwa is deleted**, as in speech: `नगर` is "nagar", not
   "nagara". Without this every transliterated token gains a trailing vowel and
   fails to match the romanised spelling actually stored in the index.

With those, 10 of 11 target place names are exact. The remaining miss —
`कोलकाता → kolakata` rather than "kolkata" — is *medial* schwa deletion, which
needs a lexicon rather than a rule, so we documented it instead of guessing. We
also keep the original script alongside the transliteration in
`retrieval_text`, so BM25 can still match on the Devanagari form.

Hindi also inverts word order for landmarks. English says "behind Shiv Mandir";
Hindi says "shiv mandir ke pichhe" — the relation *follows* the landmark.
Matching only the English order returned the *locality* as the landmark and
discarded the actual landmark, which would have quietly broken FR-09 for every
Hindi address.

### Our own metric was flattering us in two places

Both found by reading the per-field table rather than the headline number:

- **Zero-support fields.** `street` and `sub_locality` have no truth values
  anywhere in the gold set. Recall is then vacuously 1.0, so the field scores
  1.000 if we predict nothing and 0.000 if we predict anything — and averaging
  those into macro F1 moved the headline by nine points for reasons unrelated to
  resolution quality. They are now reported as *unmeasured* and excluded.
- **Stratification.** Round-robin sampling across difficulty buckets pulled
  clean rows from 7.6% of the corpus up to **37.5%** of the gold set. Every
  metric measured against it would have been optimistic, and nothing in the
  numbers themselves would have revealed it. Proportional allocation fixed it;
  a test now asserts the gold splits track the corpus distribution.

Generalising: a metric is a piece of code and deserves tests as much as the
pipeline does. Both bugs made the system look *better*, which is the direction
you are least likely to investigate.

### A cheap invariant caught an inconsistency we would never have found by eye

`build_seeds` encoded the DIGIPIN from the full-precision coordinate but stored
the coordinate rounded to six decimals. A level-10 cell is ~3.8 m across, so for
a point near a cell boundary the stored code did not match the stored
coordinate — and anything recomputing it, such as the rider app encoding offline
from `geo`, would have disagreed with us by one cell. One assertion
(`digipin == encode(geo.lat, geo.lng)`) found it; the fix is to round first, then
encode.

### Configuration A cannot auto-resolve, and that is the point

The Day 1 baseline on the dev split:

| Metric | Configuration A |
|---|---|
| Field F1 (macro) | **0.816** |
| Full-address exact match | **0.420** |
| Geocode error, median | **969 m** |
| Geocode coverage | 70.0% |
| Auto-resolution rate | **0.0%** |
| Latency p50 / p95 | 0.4 ms / 29.8 ms |

The 0% is structural, not a failure, and it is worth being able to say so
precisely. With no retrieval, `landmark_match` and `landmark_observations` are
necessarily 0 and `geo_source_tier` is capped at 0.3 (pincode centroid), so the
maximum achievable confidence is **0.590** — we measured a 0.572 maximum on dev.
The threshold is 0.80. Configuration A is therefore *incapable* of clearing it,
which is exactly the gap configurations C through E have to close.

Two things we are glad we did today rather than Saturday:

- Wrote the number down while it is bad. "0.816 → ..." only exists because
  Thursday recorded 0.816.
- Made `precision_at_threshold` report **0.0** when no address clears the
  threshold, rather than the vacuous 1.0 that "all zero of them were correct"
  technically justifies. A metric that reads perfect when nothing happened is
  worse than no metric.

### Smaller notes

- Lazy-loading the gazetteer put a one-off 270 ms table load *inside* the S1
  timing, making the stage look 300× slower than its real 0.4 ms. A `warm()`
  called during Lambda init moves the cost to cold start, where it belongs, and
  makes the per-stage numbers mean something.
- Keeping the pipeline pure — no DynamoDB, no EventBridge, no logging handlers —
  meant the whole thing was exercisable from a CLI and the test suite on Day 1,
  before any AWS access existed. The Lambda handler is 150 lines of request
  parsing around it.
- The clarification logic had a fallback that asked for the house number when
  *nothing* was missing, because it fell through the priority loop. A question
  asking for information we already have is how customers learn to ignore us; it
  now asks for a map confirmation instead, which is the right question when the
  gap is an unverified location rather than an absent field.

---

## Day 2 — Friday 18 September

### A fixed 3 km geo radius is a metro assumption, and it was excluding the right answer

The architecture document describes the geo filter as "3 km around the pincode
centroid — the highest-value filter in the system". It is the highest-value
filter in a *city*. Measured on the dev split, **29% of localities sit more than
3 km from their own pincode centroid**, with a 90th percentile of 14.8 km and a
maximum of 82 km. Rural pincodes are areas, not points.

The consequence was invisible until measured: with the hybrid signals enabled,
the correctly named landmark was being *filtered out* before scoring, and the
median geocode error went from 0 m (BM25 only, no geo filter) to over 1 km.
The radius now scales with the pincode's own estimated size, which we already
compute from its office count. A GPS hint keeps the tight radius, because a
rider is standing near the doorstep. After the change both retrieval modes give
a 0 m median.

The general lesson: a constant that is obviously right for Delhi is a *claim*
about the rest of India, and the gold set is spread across 23 cities precisely
so that such claims get tested.

### "Near X" is not "at X"

Every address that named a landmark was being geocoded *from the landmark*.
"Baulabanadh Post Office, near Nairi" is a doorstep at Baulabanadh, near Nairi
— and Baulabanadh itself was in the graph, at exactly the true coordinate. The
pipeline never looked it up, because S1 only extracts *relation* phrases and a
locality has no relation word in front of it.

The fix is to treat the locality as the **anchor**: it is searched alongside the
relation phrases, and when it matches, S4 geocodes from it with an `inside`
relation and a wider accuracy radius, ahead of any "near". That single change
moved the median geocode error of the retrieval stacks from 1,358 m to **0 m**.

### RRF rank adjacency is not ambiguity

The AMBIGUOUS rule compared the top two candidates' fused scores and called them
"near-equal" within 5%. Under Reciprocal Rank Fusion, ranks 1 and 2 in the same
signal score 1/61 and 1/62 — a 1.6% gap — *whatever the names are*. A quarter
of the dev split came back AMBIGUOUS, most of it two random nearby places.

Rivalry is now judged on **name affinity**: does the text name both candidates
about equally well? Two places found by proximity alone are never rivals. The
ambiguous rate fell from 25% to 4%, and the cases that remain are the real
ones — the two Shiv Mandirs with no pincode to separate them.

### The prompt budget is not the candidate budget

`candidates_to_model = 8` was applied inside retrieval, so only the top eight
fused candidates were ever materialised. With the proximity signal on, those
eight slots filled with nearby-but-unnamed places, and the landmark the text
actually named — first by BM25, ninth after fusion — was gone before the
affinity matcher could see it. The cap now applies only to what is *sent to the
model*; the matcher and S4 see the whole list.

### Our corpus generator was writing Hindi nobody writes

It mapped "near Nairi" to "के पास नैरि": English word order with a Hindi
postposition. Real Hindi puts the relation *after* the landmark — "नैरि के
पास". The extractor, correctly matching the postposition, then captured the
words *before* it: the pincode and a transliterated "post office". The corpus
now rewrites word order before transliterating, and the phrase cleaner strips
digits and postal jargon from both ends of a captured landmark.

### A backspace character had been silently disabling a Day 1 feature

Writing `\b` through two layers of shell and Python quoting produced a literal
U+0008 in `_SUB_LOCALITY_RE`, so the pattern could never match and
`sub_locality_of()` had returned `None` for every seed since Thursday. The
field showed as "unmeasured" in the ablation, which was true and also the only
visible symptom. Found by grepping every `.py` file for control characters,
which is now something we do after any scripted edit. `sub_locality` is
measured as of today.

### Two real bugs my own tests caught in code I had just written

- `normalise_model_output` recorded a conflict when the model disagreed with a
  validated pincode — and then `continue`d before copying the validated value
  into the result, so the one field we were *sure* of vanished from S3's
  output. The pipeline masked it (S1's value survived by a different route),
  which is exactly why the module has its own tests.
- `attach_matches` assigned candidates to phrases in retrieval order, so
  "behind shiv mandir" got whichever candidate ranked first overall — plausibly
  the Gupta store from the *other* phrase in the same address. Assignment is
  now by measured affinity, globally greedy and one-to-one.

### What retrieval alone is worth, measured honestly

Bedrock access is not yet granted and Ollama is not installed here, so the
model stacks B–E report **NOT RUN** with the exact reason rather than a number
from a stub. What *can* be measured is retrieval without a model — the two
diagnostic rows — and the result is the strongest single argument the
architecture has:

| | Geo median | Geo p90 | Coverage |
|---|---|---|---|
| A — no retrieval | 969 m | 18,449 m | 70% |
| R1 — + BM25 retrieval | **0 m** | 2,388 m | 87% |
| R2 — + vector + geo | **0 m** | 2,895 m | 89% |

Field F1 is unchanged (0.826) across all three, as it should be: retrieval does
not extract fields, it *places* them. That separation is the point of running
the diagnostics — when the model stacks do run, any F1 gain is attributable to
S3 and any geocode gain to S2, and neither can take credit for the other.

Two honest caveats travel with those numbers. The local vector signal is a
character-trigram hashing model, not Titan, so R2 is a lower bound. And the
landmark graph was warmed from the same directory the addresses came from — the
"0 m" says the graph *works* when it knows the place, not that it knows every
place.

### Smaller notes

- `OpenSearch _msearch` is one HTTP round trip carrying three query bodies. It
  satisfies "one round trip" (NFR-04) while returning each signal's ranking
  separately, which RRF needs and a blended query cannot provide.
- Delivery chatter ("call before coming") is now stripped in S0, before the
  cache key is hashed. Two submissions of the same address with and without it
  are an exact cache hit rather than two pipeline runs.
- In `aws` mode the near-duplicate cache is off: it would cost a Titan call on
  every request *before* knowing whether the cache hits. It is on in local mode
  where the embedder is free. The hit rate will decide whether that changes.
